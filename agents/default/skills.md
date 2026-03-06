# Skills

<!-- Define agent-specific skills and workflows here. -->
<!-- Each skill should include a name, description, and step-by-step instructions. -->
<!-- The agent will load these skills as reference during conversations. -->
<!-- Leave this file empty if no custom skills are needed. -->

## GCloud Emergency Ops

Use this workflow when the user asks you to investigate or fix an existing Google Cloud / Cloud Run service.

1. Identify the target project, region, and service or job name.
2. Start with read-only inspection:
   - `gcloud config list`
   - `gcloud run services describe <service> --region <region>`
   - `gcloud run revisions list --service <service> --region <region>`
   - `gcloud run logs read <service> --region <region> --limit 100`
3. Summarize the current state before proposing action.
4. If a change is needed, state the exact command and expected impact.
5. After a change, verify the service status and recent logs.
