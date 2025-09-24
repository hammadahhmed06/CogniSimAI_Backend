from uuid import uuid4
from fastapi.testclient import TestClient
from app.main import app  # FastAPI app entry point

# NOTE: This test is illustrative and may require test fixtures/mocks for Supabase.
# It asserts that /api/issues excludes issues from archived projects unless include_archived_projects=true.

def test_list_issues_excludes_archived_projects(monkeypatch):
    client = TestClient(app)

    # Minimal auth stub: inject a fake user into dependency
    from app.core.dependencies import get_current_user, UserModel
    app.dependency_overrides[get_current_user] = lambda: UserModel(id=uuid4(), email="test@example.com")

    # Monkeypatch Supabase client minimal behavior
    from app.core import dependencies as deps
    from app.api.routes import issues as issues_module

    class FakeQuery:
        def __init__(self, rows):
            self._rows = rows
        def select(self, *args, **kwargs):
            return self
        def eq(self, *args, **kwargs):
            return self
        def in_(self, *args, **kwargs):
            return self
        def order(self, *args, **kwargs):
            return self
        def limit(self, *args, **kwargs):
            return self
        def maybe_single(self):
            return self
        def execute(self):
            class R: pass
            r = R()
            r.data = self._rows
            return r

    class FakeClient:
        def __init__(self):
            self._tables = {}
        def table(self, name):
            class T:
                def __init__(self, parent, name):
                    self.parent = parent
                    self.name = name
                    self._rows = parent._tables.get(name, [])
                def select(self, *args, **kwargs):
                    return FakeQuery(self._rows)
            return T(self, name)

    fake = FakeClient()
    # Seed: issues belong to project A (active) and project B (archived)
    project_a = str(uuid4())
    project_b = str(uuid4())
    fake._tables['issues'] = [
        { 'id': str(uuid4()), 'issue_key': 'A-1', 'title': 'Active 1', 'project_id': project_a, 'owner_id': 'x' },
        { 'id': str(uuid4()), 'issue_key': 'B-1', 'title': 'Archived 1', 'project_id': project_b, 'owner_id': 'x' },
        { 'id': str(uuid4()), 'issue_key': 'NO-1', 'title': 'Orphan', 'project_id': None, 'owner_id': 'x' },
    ]
    fake._tables['projects'] = [
        { 'id': project_a, 'status': 'active', 'owner_id': 'x' },
        { 'id': project_b, 'status': 'archived', 'owner_id': 'x' },
    ]

    deps.supabase = fake  # type: ignore
    issues_module.supabase = fake  # type: ignore

    # Call without include_archived_projects -> should exclude B-1, keep A-1 and NO-1
    res = client.get('/api/issues')
    assert res.status_code == 200
    items = res.json().get('items', [])
    keys = {i['issue_key'] for i in items}
    assert 'A-1' in keys
    assert 'NO-1' in keys
    assert 'B-1' not in keys

    # With include_archived_projects=true -> keep B-1 as well
    res2 = client.get('/api/issues?include_archived_projects=true')
    assert res2.status_code == 200
    keys2 = {i['issue_key'] for i in res2.json().get('items', [])}
    assert 'B-1' in keys2

    app.dependency_overrides.clear()
