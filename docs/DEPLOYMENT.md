# Deployment

The repository includes a Render Blueprint in `render.yaml`. It deploys one Docker
web service in Singapore, checks `/ready`, and mounts a 1 GB persistent disk at
`/data` for SQLite state.

## Cost boundary

This Blueprint uses a paid Render web-service plan because Render persistent disks
are not available to free web services. Review Render's current price estimate
before applying the Blueprint. Creating or deleting cloud resources is intentionally
not automated by this repository.

## Deploy from GitHub

1. Connect `mingyoud2003-bot/cross-border-travel-agent` to your Render account.
2. In the Render Dashboard, choose **New > Blueprint**, select the repository, and
   review the resources parsed from `render.yaml`.
3. Enter `OPENAI_API_KEY` when Render prompts for the unsynced secret. Never commit
   the value to Git or place it directly in `render.yaml`.
4. Confirm the monthly price and apply the Blueprint.
5. Wait for both the Docker deploy and `/ready` health check to succeed.

The Blueprint enables automatic deploys only after GitHub checks pass. It deliberately
runs one instance: the current rate limiter is process-local, while the SQLite disk
cannot be shared safely across horizontally scaled instances.

## Smoke test

Set `SERVICE_URL` to the Render URL, without a trailing slash:

```bash
curl --fail "$SERVICE_URL/health"
curl --fail "$SERVICE_URL/ready"
curl --fail "$SERVICE_URL/metrics"
```

Then open `SERVICE_URL` in a browser, create a session, and run the example request
from the README. A successful deployment should return an eight-section decision,
persist the session across a service restart, and expose no message content or API
key in logs or metrics.

## Production safeguards

- Use a dedicated OpenAI project for this deployment and configure project-level
  spend and rate limits.
- Keep staging and production keys separate.
- Treat `/metrics` and session trace endpoints as portfolio-demo interfaces. Add
  authentication or network restrictions before serving real user data.
- Back up or export session data before replacing the persistent disk.
- To roll back application code, redeploy a previously successful commit in Render.
  Database schema changes must remain backward-compatible.

## Remove the deployment

Delete the Blueprint-managed service and its disk from Render when it is no longer
needed. Deleting the disk permanently removes its SQLite sessions; export anything
you need first.
