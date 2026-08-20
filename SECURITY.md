# Security policy

## Supported version

Security fixes are applied to the latest revision on `main`.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose local files, credentials, Codex sessions, or arbitrary command execution. Use GitHub's private vulnerability reporting feature on the repository's **Security** tab. If private reporting is not enabled, contact the repository owner privately before disclosing details.

Include the affected revision, reproduction steps, impact, and any suggested mitigation. Please avoid accessing data that is not yours while validating a report.

## Local trust boundary

Codex Bots is a single-user localhost application. It has no account system or network authentication and must not be exposed directly to the public internet. The Codex runtime inherits the local user's authentication, configuration, and approved integrations. Bot-created files and attachments stay inside the configured local workspace by default.
