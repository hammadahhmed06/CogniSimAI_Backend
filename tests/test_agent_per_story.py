import pytest
from unittest.mock import patch, AsyncMock
from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import UserModel, TeamContext, get_current_user, get_team_context
from fastapi import HTTPException

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


@patch('app.api.routes.agents._get_run_and_item_or_404')
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
def test_per_story_commit_and_regen(mock_client_obj, Agent, Runner, AsyncOpenAI, ed_quality, ed_cos, ed_embed, cos, fetch, qual, embed, est, supabase_ed, supabase_api, mock_get_run_item, client):
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
        'issue_key': 'OR-2',
        'project_id': str(uuid4()),
        'workspace_id': str(uuid4()),
        'epic_id': e_id,
        'owner_id': str(GLOBAL_UID),
    }
    supabase_api.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
        'id': e_id,
        'title': 'Epic',
        'type': 'epic',
        'issue_key': 'OR-2',
        'project_id': str(uuid4()),
        'workspace_id': str(uuid4()),
        'epic_id': e_id,
        'owner_id': str(GLOBAL_UID),
    }
    # runs count (limit)
    supabase_api.table.return_value.select.return_value.eq.return_value.gte.return_value.lt.return_value.execute.return_value.data = []
    supabase_api.table.return_value.select.return_value.eq.return_value.gte.return_value.lt.return_value.execute.return_value.error = None
    # persist run insert
    supabase_api.table.return_value.insert.return_value.execute.return_value.data = [{'id': str(uuid4())}]
    supabase_api.table.return_value.insert.return_value.execute.return_value.error = None
    # children fetch inside create issue and duplicates
    supabase_api.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    supabase_api.table.return_value.select.return_value.eq.return_value.execute.return_value.error = None
    # persist run items insert
    supabase_api.table.return_value.insert.return_value.execute.return_value.data = []
    supabase_api.table.return_value.insert.return_value.execute.return_value.error = None
    # run update
    supabase_api.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []
    supabase_api.table.return_value.update.return_value.eq.return_value.execute.return_value.error = None

    # Act: generate
    resp = client.post('/api/agents/epic/decompose', json={ 'epic_id': e_id, 'max_stories': 3 })
    assert resp.status_code == 200
    data = resp.json()
    assert data['stories'] and data['run_id']
    assert data['epic_issue_key'] == 'OR-2'

    # Accept issue key strings without requiring UUID copy/paste
    resp_issue_key = client.post('/api/agents/epic/decompose', json={ 'epic_id': 'OR-2', 'max_stories': 3 })
    assert resp_issue_key.status_code == 200
    data_issue_key = resp_issue_key.json()
    assert data_issue_key['epic_issue_key'] == 'OR-2'

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
    supabase_api.table.return_value.insert.return_value.execute.return_value.error = None
    mock_get_run_item.side_effect = [
        ({'id': run_id, 'epic_id': e_id, 'created_issue_ids': []}, {'id': item_id, 'run_id': run_id, 'title': 'Login story', 'acceptance_criteria': ['User can login'], 'status': 'proposed', 'regen_count': 0}),
        ({'id': run_id, 'epic_id': e_id, 'created_issue_ids': [str(uuid4())]}, {'id': item_id, 'run_id': run_id, 'title': 'Login story', 'acceptance_criteria': ['User can login'], 'status': 'created', 'regen_count': 1, 'created_issue_id': str(uuid4())}),
    ]

    resp2 = client.post(f'/api/agents/runs/{run_id}/items/{item_id}/commit', json={'title':'Login story','acceptance_criteria':['User can login']})
    assert resp2.status_code == 200
    # regenerate one (should now fail because created)
    resp3 = client.post(f'/api/agents/runs/{run_id}/items/{item_id}/regenerate', json={'feedback':'make criteria clearer'})
    assert resp3.status_code in (400, 409)


@patch('app.api.routes.agents.epic_decomposer.regenerate_story', new_callable=AsyncMock)
@patch('app.api.routes.agents.supabase')
@patch('app.api.routes.agents._get_run_and_item_or_404')
def test_regenerate_uses_feedback(mock_get_run_item, mock_supabase, mock_regen, client):
    """Ensure regenerate endpoint forwards original story context and feedback to agent."""
    run_id = uuid4()
    item_id = uuid4()
    epic_id = uuid4()
    mock_get_run_item.return_value = (
        {
            'id': str(run_id),
            'epic_id': str(epic_id),
            'output': {},
        },
        {
            'id': str(item_id),
            'run_id': str(run_id),
            'title': 'Initial story',
            'acceptance_criteria': ['Dark theme available', 'Admins can toggle theme'],
            'status': 'proposed',
            'regen_count': 0,
        }
    )

    # supabase table chain mocks
    table_mock = mock_supabase.table.return_value
    # daily usage query
    table_mock.select.return_value.eq.return_value.gte.return_value.lt.return_value.execute.return_value.data = []
    # epic fetch
    table_mock.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
        'id': str(epic_id),
        'title': 'Epic title',
        'description': 'As a user I want themes.',
    }
    # run item update / run update operations to no-op
    table_mock.update.return_value.eq.return_value.execute.return_value.data = []

    mock_regen.return_value = {
        'success': True,
        'data': {'stories': [{'title': 'Updated story', 'acceptance_criteria': ['Light theme available']}]},
        'warnings': [],
        'duplicate_matches': [],
    }

    resp = client.post(
        f'/api/agents/runs/{run_id}/items/{item_id}/regenerate',
        json={'feedback': 'consider theme of website light'},
    )
    assert resp.status_code == 200

    await_args = mock_regen.await_args
    assert await_args is not None
    args, kwargs = await_args
    assert kwargs['feedback'] == 'consider theme of website light'
    assert 'Initial story' in kwargs['original_story']['title']
    assert 'Dark theme available' in kwargs['original_story']['acceptance_criteria'][0]


@patch('app.api.routes.agents._create_child_issue', side_effect=HTTPException(status_code=502, detail='insert failed'))
@patch('app.api.routes.agents.supabase')
@patch('app.api.routes.agents._get_run_and_item_or_404')
def test_commit_failure_propagates(mock_get_run_item, mock_supabase, mock_create_issue, client):
    run_id = uuid4()
    item_id = uuid4()
    epic_id = uuid4()

    mock_get_run_item.return_value = (
        {'id': str(run_id), 'epic_id': str(epic_id), 'created_issue_ids': []},
        {'id': str(item_id), 'run_id': str(run_id), 'title': 'Story', 'acceptance_criteria': ['AC'], 'status': 'proposed'}
    )

    table_mock = mock_supabase.table.return_value
    table_mock.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
        'id': str(epic_id),
        'title': 'Epic title',
        'type': 'epic',
        'owner_id': str(GLOBAL_UID),
    }

    resp = client.post(f'/api/agents/runs/{run_id}/items/{item_id}/commit', json={'title': 'Story', 'acceptance_criteria': ['AC']})
    assert resp.status_code == 502
    assert resp.json()['detail'] == 'insert failed'
