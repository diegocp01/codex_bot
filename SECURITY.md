# Security policy

## Supported version

Security fixes are applied to the latest revision on `main`.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose local files, credentials, Codex sessions, or arbitrary command execution. Use GitHub's private vulnerability reporting feature on the repository's **Security** tab. If private reporting is not enabled, contact the repository owner privately before disclosing details.

Include the affected revision, reproduction steps, impact, and any suggested mitigation. Please avoid accessing data that is not yours while validating a report.

## Local trust boundary

Codex Bots is a single-user localhost application. It has no account system and must not be exposed directly to the public internet. The server restricts accepted hosts and origins, while browser mutations require an unguessable per-launch request token. These defenses protect the localhost trust boundary; they are not a substitute for hosted authentication.

The Codex runtime inherits the local user's authentication, configuration, and approved integrations. Bot-created files stay inside the configured local workspace by default. Attachments, routine state, run records, and per-Bot browser profiles live under the Git-ignored `instance/` directory.

The Bot browser accepts public HTTP(S) pages only. It blocks private-network targets, downloads, password filling, and clicks whose visible labels indicate consequential external actions such as purchasing, publishing, deleting, or transferring. Web content remains untrusted and can attempt prompt injection, so users should review browser results and complete sensitive actions manually.
