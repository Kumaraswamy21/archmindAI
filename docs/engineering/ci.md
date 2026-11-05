# CI and quality gates

The [quality workflow](../../.github/workflows/ci.yml) runs on pushes, pull requests, and manual dispatch. It uses read-only repository permission, does not persist checkout credentials, and receives no database or provider secrets.

| Job | Required checks |
| --- | --- |
| Python lint and formatting | Ruff checks all backend application, test, and script files. The formatting gate checks all new or edited Python files. |
| Backend pytest | Runs the backend suite with `DATABASE_URL` and `GEMINI_API_KEY` empty. Remote integration tests remain skipped. |
| API contract and runtime prompts | Validates the generated and tracked OpenAPI 3.1 schema; checks every prompt release, active pointer, digest, variable list, and template path. |
| Frontend | Installs with `npm ci` from `package-lock.json`, then runs ESLint, TypeScript, Vitest, API type generation comparison, and the Next.js production build. |

Each failed command fails its job; a short status is written to the GitHub job summary. The workflow does not deploy, tag, commit, or write to a repository.

## Python formatting baseline

The backend has 126 unchanged legacy Python files that Ruff would reformat. `backend/format-baseline.json` stores their content hashes. `python scripts/check_format.py` skips only those exact bytes. It checks every new file and any legacy file whose bytes change. This keeps formatting work in the files being edited while still making formatting a required gate. The baseline can shrink as files are formatted; do not refresh it merely to bypass a failure.

## Prompt and API updates

Run `python -m app.prompts.validate` from `backend` before releasing prompt changes. Add a semantic version directory with `system.md` and `user.md`, record each template's SHA-256 and variables in `registry.toml`, and update the active references. Existing released files are immutable. The validator checks inactive releases too and rejects unregistered files or directories.

When the backend API changes, run `python scripts/check_openapi.py --write` from `backend`, then `npm run api:types` from `frontend`. CI fails if either tracked generated artifact differs from the runtime schema. `check_openapi.py` also validates both schemas against OpenAPI 3.1.

## Remote PostgreSQL integration

The [remote integration workflow](../../.github/workflows/integration-postgres.yml) runs only by manual dispatch with the confirmation input enabled. Configure the protected `ci-isolated-postgres` GitHub environment with `CI_ISOLATED_DATABASE_URL` as a secret and `CI_ISOLATED_DATABASE_NAME` as a variable. The database name must start with `archmind_ci_`, and the URL must name that exact remote database. The test creates and drops uniquely named schemas inside it. Use a dedicated test database with no production data. The job uses deterministic embeddings and needs no Gemini credential.

Local equivalents from the repository root:

```sh
cd backend
python -m pip install -e '.[test,quality]'
ruff check app tests scripts
python scripts/check_format.py
python -m pytest -q
python scripts/check_openapi.py
python -m app.prompts.validate
cd ../frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run api:types
npm run build
```
