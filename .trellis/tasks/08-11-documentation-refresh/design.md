# Documentation information architecture

## Reader path

```
README
├── docs/getting-started/  First local run and configuration choices
├── docs/interfaces/       MCP and browser/API contracts
├── docs/integrations/     Optional channel bridge
└── docs/deployment/       General rollout, frontend, and specialist runbooks
```

The root README remains the concise English entrance. The Chinese README is a
parallel concise entrance for Chinese-speaking users. Neither is an operations
manual. They share the same navigation and link into the English technical
documentation, whose existing content is predominantly Chinese or bilingual.

## Placement rules

- `getting-started`: prerequisites, launcher profiles, environment selection,
  and first successful verification.
- `interfaces`: externally consumed protocol and HTTP contracts.
- `integrations`: optional third-party installation and channel-specific
  operation.
- `deployment`: repeatable deployments and production variants. General
  deployment lives before frontend and provider/network-specific runbooks.

Each folder receives an `README.md` index when it contains several documents;
`docs/README.md` is the documentation landing page. Existing filenames are
renamed only when their new path makes their purpose clearer.

## Compatibility

Documentation is versioned with the repository, so old local paths need not be
kept as duplicate redirect files. Every in-repository Markdown link and the
`.env.example` discovery comment will use the new canonical paths.
