import pytest
from unittest.mock import patch
from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import UserModel, TeamContext, get_current_user, get_team_context

GLOBAL_UID = uuid4()


@pytest.fixture(autouse=True)
def override_deps():
    tid = uuid4()
    app.dependency_overrides[get_current_user] = lambda: UserModel(id=GLOBAL_UID, email="test@example.com")
    app.dependency_overrides[get_team_context] = lambda: TeamContext(team_id=tid, role="lead")
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


def supabase_chain(mock, data):
    m = mock.table.return_value
    return m


@patch('app.api.routes.agents.supabase')
@patch('app.agents.epic_decomposer.supabase')
@patch('app.api.routes.agents.estimate_tokens')
@patch('app.api.routes.agents.embed_texts')
@patch('app.api.routes.agents.compute_quality_score')
@patch('app.api.routes.agents.fetch_issue_embeddings')
@patch('app.api.routes.agents.cosine_sim')
@patch('app.agents.epic_decomposer.embed_texts')
@patch('app.agents.epic_decomposer.cosine_sim')
@patch('app.agents.epic_decomposer.compute_quality_score')
@patch('app.agents.epic_decomposer.AsyncOpenAI')
@patch('app.agents.epic_decomposer.Runner')
@patch('app.agents.epic_decomposer.Agent')
@patch('app.agents.epic_decomposer.client')
@patch('app.agents.epic_decomposer.GEMINI_API_KEY', 'test')
def test_per_story_commit_and_regen(mock_client_obj, Agent, Runner, AsyncOpenAI, ed_quality, ed_cos, ed_embed, cos, fetch, qual, embed, est, supabase_ed, supabase_api, client):
    # Arrange model to return a minimal valid JSON for decompose/regenerate
    class R:
        final_output = '{"stories":[{"title":"Login story","acceptance_criteria":["User can login","Error on wrong password"]}]}'
    Runner.run.return_value = R()
    est.return_value = 100
    qual.return_value = 0.9
    ed_quality.return_value = 0.9

    # Mock Supabase chains for epic validation and run persistence
    # epic fetch
    e_id = str(uuid4())
    supabase_api.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
        'id': e_id,
        'title': 'Epic',
        'type': 'epic',
        'project_id': str(uuid4()),
        'workspace_id': str(uuid4()),
        'epic_id': e_id,
        'owner_id': str(GLOBAL_UID),
    }
    # runs count (limit)
    supabase_api.table.return_value.select.return_value.eq.return_value.gte.return_value.lt.return_value.execute.return_value.data = []
    # persist run insert
    supabase_api.table.return_value.insert.return_value.execute.return_value.data = [{'id': str(uuid4())}]
    # children fetch inside create issue and duplicates
    supabase_api.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    # persist run items insert
    supabase_api.table.return_value.insert.return_value.execute.return_value.data = []
    # run update
    supabase_api.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []

    # Act: generate
    resp = client.post('/api/agents/epic/decompose', json={ 'epic_id': e_id, 'max_stories': 3 })
    assert resp.status_code == 200
    data = resp.json()
    assert data['stories'] and data['run_id']

    run_id = data['run_id']
    # list items
    supabase_api.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = [
        {'id': str(uuid4()), 'run_id': run_id, 'item_index': 0, 'title': 'Login story', 'acceptance_criteria': ['User can login'], 'status': 'proposed'}
    ]
    items = client.get(f'/api/agents/runs/{run_id}/items')
    assert items.status_code == 200
    item_id = items.json()[0]['id']

    # commit one
    supabase_api.table.return_value.insert.return_value.execute.return_value.data = [{'id': str(uuid4())}]
    resp2 = client.post(f'/api/agents/runs/{run_id}/items/{item_id}/commit', json={'title':'Login story','acceptance_criteria':['User can login']})
    assert resp2.status_code == 200
    # regenerate one (should now fail because created)
    resp3 = client.post(f'/api/agents/runs/{run_id}/items/{item_id}/regenerate', json={'feedback':'make criteria clearer'})
    assert resp3.status_code in (400, 409)
