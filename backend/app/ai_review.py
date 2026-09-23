"""Optional server-side LLM review. It never changes quantities or approves orders."""
from copy import deepcopy
import json
import os
from urllib.parse import urlparse

import httpx
from model.decision.rules import build_ai_context, validate_ai_judgement


def configuration():
    base = os.getenv('TIREK_LLM_BASE_URL', '').strip().rstrip('/')
    model = os.getenv('TIREK_LLM_MODEL', '').strip()
    key = os.getenv('TIREK_LLM_API_KEY', '').strip()
    parsed = urlparse(base)
    local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
    valid_url = bool(parsed.hostname) and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
    available = bool(valid_url and model and (key or local) and (parsed.scheme == 'https' or local and parsed.scheme == 'http'))
    return {'available': available, 'base': base, 'model': model, 'key': key}


def unavailable(message):
    return {'status': 'unavailable', 'verdict': None, 'reasons': [message],
            'evidence_ids': [], 'rule_ids': [], 'suggested_action': None, 'provider_model': None}


def review(detail):
    config = configuration()
    if not config['available']:
        return unavailable('ИИ-провайдер не настроен. Укажите TIREK_LLM_BASE_URL, TIREK_LLM_MODEL и ключ в backend/.env.')
    item, meta = detail['item'], detail['meta']
    evidence = deepcopy(item['evidence'])
    for label, key in [('Свободный остаток', 'free_stock'), ('Поступление на горизонт', 'inbound_within_horizon'), ('Итоговое количество', 'final_quantity')]:
        evidence.append({'id': item['item_id'] + '-' + key, 'source_kind': 'manual',
                         'reference': 'calculation:' + meta['calculation_id'], 'label': label,
                         'value': item[key], 'unit': item['unit']})
    context = build_ai_context(
        evidence=evidence,
        versions={'dataset_version': meta['dataset_version'], 'policy_version': meta['policy_version'],
                  'calculation_revision': meta['revision']},
        recommended_quantity=item['final_quantity'],
        product_text=json.dumps({'name': item['name'], 'decision_status': item['decision_status'],
                                 'reason': item['reason'], 'issues': [issue['message'] for issue in item['issues']]}, ensure_ascii=False),
        allowed_rule_ids=list(dict.fromkeys([*detail['applied_rule_ids'], 'AI-01'])),
    )
    instruction = context['instruction'] + (
        '\nОтветь только JSON на русском. Поля: status="reviewed", verdict (supports/needs_review/recalculate), '
        'reasons (список строк), evidence_ids и rule_ids (только ID из контекста), suggested_action '
        '(одно из allowed_actions либо null), provider_model=' + json.dumps(config['model']) + '. '
        'Не цитируй числа, которых нет в числовых evidence. Не считай неизвестный остаток нулём. '
        'При отсутствии данных для заказа verdict не может быть supports.'
    )
    headers = {'Authorization': 'Bearer ' + config['key']} if config['key'] else {}
    payload = {'model': config['model'], 'messages': [
        {'role': 'system', 'content': instruction},
        {'role': 'user', 'content': json.dumps(context, ensure_ascii=False, allow_nan=False)},
    ], 'response_format': {'type': 'json_object'}, 'max_completion_tokens': 1000}
    for attempt in range(2):
        try:
            with httpx.Client(timeout=httpx.Timeout(25, connect=5), follow_redirects=False) as client:
                response = client.post(config['base'] + '/chat/completions', headers=headers, json=payload)
                response.raise_for_status()
                choice = response.json()['choices'][0]
                if choice.get('finish_reason') != 'stop':
                    return unavailable('ИИ не завершил ответ. Повторите проверку.')
                result = validate_ai_judgement(choice['message']['content'], request_context=context,
                                               current_context=context, provider_called=True,
                                               provider_model=config['model'])
                if item['decision_status'] == 'needs_data' and result['verdict'] == 'supports':
                    return unavailable('Ответ ИИ противоречит отсутствующим данным. Рекомендация не изменена.')
                if result['status'] != 'reviewed':
                    return unavailable('Ответ ИИ не прошёл проверку формата или ссылок на факты. Рекомендация не изменена.')
                return result
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt == 0:
                continue
            return unavailable('Нет ответа от ИИ-провайдера. Прогноз и расчёт сохранены.')
        except httpx.HTTPStatusError as exc:
            return unavailable(f'ИИ-провайдер отклонил запрос (HTTP {exc.response.status_code}). Проверьте настройки и доступ модели.')
        except (KeyError, IndexError, TypeError, ValueError):
            return unavailable('ИИ вернул неподдерживаемый формат. Прогноз и расчёт сохранены.')


def review_calculation(result):
    # Explicit request only; avoid thousands of unbounded provider calls per import.
    for index, item in enumerate(result['response']['items']):
        judgement = review(result['details'][item['item_id']]) if index < 5 else {
            'status': 'not_requested', 'verdict': None,
            'reasons': ['Пакетная проверка ограничена первыми пятью позициями. Остальные можно проверить в карточке.'],
            'evidence_ids': [], 'rule_ids': [], 'suggested_action': None, 'provider_model': None,
        }
        item['ai'] = judgement
        result['details'][item['item_id']]['item']['ai'] = deepcopy(judgement)
