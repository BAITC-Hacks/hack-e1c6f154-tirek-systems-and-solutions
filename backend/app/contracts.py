"""Validate against the shared contract instead of maintaining a second schema."""
from pathlib import Path
import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SPEC = yaml.safe_load((ROOT / 'contracts/openapi.yaml').read_text(encoding='utf-8'))


class DomainError(Exception):
    def __init__(self, code, message, status=422, details=None):
        self.code, self.message, self.status = code, message, status
        self.details = details or []


def validate(name, value):
    schema = {'$ref': f'#/components/schemas/{name}', 'components': SPEC['components']}
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
    if errors:
        raise DomainError('INVALID_PARAMETERS', 'Проверьте заполнение полей.', details=[
            {'field': '.'.join(map(str, e.absolute_path)) or None, 'message': e.message}
            for e in errors[:10]
        ])
    return value
