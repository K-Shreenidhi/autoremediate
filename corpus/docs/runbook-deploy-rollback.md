# Runbook: Deploy and rollback

## Deploying
Merge the approved pull request, wait for the pipeline to finish, then deploy to staging and run the smoke tests before production.

## Rolling back
Redeploy the previous release tag. After a rollback, open an incident review to understand what failed and write a post-mortem.