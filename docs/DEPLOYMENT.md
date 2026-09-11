# Deployment

The repository includes two Render Blueprints:

- `render.yaml` is the default portfolio deployment. It uses Render's free web
  service plan and ephemeral SQLite storage.
- `render.paid.yaml` is the production-shaped reference. It uses a paid web service
  and a 1 GB persistent disk for SQLite state.

Both variants deploy one Docker service in Singapore, check `/ready`, inject the
OpenAI key as an unsynced secret, and deploy updates only after GitHub checks pass.

## Free portfolio deployment

1. Connect `mingyoud2003-bot/cross-border-travel-agent` to your Render account.
2. In the Render Dashboard, choose **New > Blueprint** and select the repository.
3. Keep the Blueprint path at the root default, `render.yaml`.
4. Enter `OPENAI_API_KEY` when Render prompts for the unsynced secret. Never commit
   the value to Git or place it directly in a Blueprint.
5. Review the configuration and apply the Blueprint.

Render's free web service can spin down after an idle period and uses an ephemeral
filesystem. Consequently, the first request after inactivity can be slow, and SQLite
sessions are lost on a spin-down, restart, or deploy. Multi-turn state remains fully
functional while the instance is running. OpenAI API usage is billed separately and
is not covered by Render's free compute plan.

## Smoke test

Set `SERVICE_URL` to the Render URL, without a trailing slash:

```bash
curl --fail "$SERVICE_URL/health"
curl --fail "$SERVICE_URL/ready"
curl --fail "$SERVICE_URL/metrics"
```

Then open `SERVICE_URL` in a browser, create a session, and run the example request
from the README. A successful deployment should return an eight-section decision
and expose no message content or API key in logs or metrics.

## Paid persistent deployment

`render.paid.yaml` documents the production-shaped single-instance topology. To
deploy it as a separate Blueprint, explicitly select that file as the Blueprint path
and review Render's current price before applying it. The paid variant mounts `/data`
and persists SQLite sessions across service restarts and deployments.

It deliberately remains single-instance: the current rate limiter is process-local,
and a Render persistent disk cannot be attached to multiple instances. Migrating to
a shared database and distributed rate limiter is required before horizontal scaling.

## Production safeguards

- Use a dedicated OpenAI project and configure conservative project-level spend and
  rate limits before exposing the URL publicly.
- Keep staging and production keys separate.
- Treat `/metrics` and session trace endpoints as portfolio-demo interfaces. Add
  authentication or network restrictions before serving real user data.
- For the paid variant, back up or export session data before replacing its disk.
- To roll back application code, redeploy a previously successful commit in Render.
  Database schema changes must remain backward-compatible.

## Remove the deployment

Delete the Blueprint-managed service when it is no longer needed. For the paid
variant, deleting its disk permanently removes SQLite sessions, so export anything
you need first.
