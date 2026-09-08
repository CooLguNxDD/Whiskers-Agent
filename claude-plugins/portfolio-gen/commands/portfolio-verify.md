---
description: Run the CatPortfolio verification gate (compile + lint + test + build)
---

Run the full CatPortfolio verification gate from the repo root and report results:

```
npm run compile:layout
npm run check:layout
npm run lint
npm run test
npm run build
```

Run them in order; stop at the first failure, diagnose it, and report the exact
failing command with its output. If `check:layout` fails, the fix is usually
`npm run compile:layout` followed by staging `src/content/layout.json` — never
hand-edit the JSON. All five green = the working tree is shippable.
