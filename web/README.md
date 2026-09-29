# AgentGate Web Frontend

Vue 3 + Vite + TypeScript + vue-i18n (zh/en) single-page app for the AgentGate platform
(doc 33). The production build (`npm run build` -> `dist/`) is served by the FastAPI backend
(`agentgate web`) at `/` -- one origin, no separate static host.

## Develop

```bash
npm install
npm run dev        # http://localhost:5173, /api proxied to 127.0.0.1:8030
agentgate web      # in another shell
```

## Pages

login/register -> banks list ([detail]/[overview]) -> bank detail (cases + my config +
Excel + autoadapt) -> new-bank wizard -> launch run -> queue & history -> run detail
(progress / bilingual report / trajectory analysis / artifacts) -> report center -> compare
-> admin (users + settings).

## Build

```bash
npm run build      # writes dist/; backend picks it up on next start
```
