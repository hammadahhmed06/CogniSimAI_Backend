import uuid
from uuid import UUID
import os
import pytest
import requests

BASE_URL = os.getenv('TEST_BASE_URL','http://127.0.0.1:8000')
TEST_TOKEN = os.getenv('TEST_USER_TOKEN','')
HEADERS = { 'Authorization': f'Bearer {TEST_TOKEN}', 'Content-Type': 'application/json' }

@pytest.mark.skipif(not TEST_TOKEN, reason='TEST_USER_TOKEN env var required for authenticated tests')
def test_prompt_variant_crud_and_metrics():
    # Create variant
    vid = str(uuid.uuid4())
    r = requests.post(f"{BASE_URL}/api/agents/prompt_variants", json={
        'id': vid,
        'name': 'Test Variant A',
        'template': 'You are a helpful epic decomposer test variant.',
        'active': True,
        'is_default': True
    }, headers=HEADERS)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data['id'] == vid

    # List variants
    r2 = requests.get(f"{BASE_URL}/api/agents/prompt_variants", headers=HEADERS)
    assert r2.status_code == 200
    variants = r2.json()
    assert any(v['id'] == vid for v in variants)

    # Allocation call referencing created id
    r3 = requests.get(f"{BASE_URL}/api/agents/prompt_variants/allocate?requested_variant_id={vid}", headers=HEADERS)
    assert r3.status_code == 200
    alloc = r3.json()
    assert alloc['chosen_variant_id'] == vid

    # Metrics (will have 0 runs initially)
    r4 = requests.get(f"{BASE_URL}/api/agents/prompt_variants/metrics", headers=HEADERS)
    assert r4.status_code == 200
    metrics = r4.json()
    assert isinstance(metrics, list)

