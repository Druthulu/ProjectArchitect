---
name: Deploying the site
description: Build, then sync the out/ folder
type: reference
---

1. `npm run build`
2. `rsync -a out/ host:/srv/site/`
