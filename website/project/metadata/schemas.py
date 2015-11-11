import os
import json

def _name_to_id(name):
    return ' '.join(name).split('_')


def ensure_schema_structure(schema):
    schema['pages'] = schema.get('pages', [])
    schema['title'] = schema['name']
    schema['version'] = schema.get('version', 1)
    return schema

here = os.path.split(os.path.abspath(__file__))[0]

def from_json(fname):
    with open(os.path.join(here, fname)) as f:
        return json.load(f)

OSF_META_SCHEMAS = [
    ensure_schema_structure(from_json('osf-open-ended-1.json')),
    ensure_schema_structure(from_json('osf-standard-1.json')),
    ensure_schema_structure(from_json('brandt-prereg-1.json')),
    ensure_schema_structure(from_json('brandt-postcomp-1.json')),
    ensure_schema_structure(from_json('osf-open-ended-2.json')),
    ensure_schema_structure(from_json('osf-standard-2.json')),
    ensure_schema_structure(from_json('brandt-prereg-2.json')),
    ensure_schema_structure(from_json('brandt-postcomp-2.json')),
    ensure_schema_structure(from_json('prereg-prize.json')),
]
