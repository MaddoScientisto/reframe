# Mock dashboard API

Run the Vue dashboard against a local seeded API before deploying:

```powershell
npm run mock
npm run dev
```

The mock API listens on `http://127.0.0.1:8090`. Vite proxies `/api`, `/photos`, and `/dithered` to it, so the browser uses the same paths as production. Open the Vite URL shown by `npm run dev`.

The server includes canonical hash-ID records, one legacy numeric record, pagination, carousel filtering, capture, delete, preview-save, settings, image paths, and the operational endpoints needed by the dashboard shell. State is in memory and resets when the mock process restarts.

Run the frontend contract checks with:

```powershell
npm test
```
