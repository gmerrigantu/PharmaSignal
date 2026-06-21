# PharmaSignal Next.js Frontend

Modern App Router frontend for the PharmaSignal adverse-event intelligence platform.
The existing Streamlit dashboard remains unchanged under `dashboard/`.

## Local Development

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000.

Without configuration, the app uses the built-in demo payload in `lib/demo-data.ts`.

## AWS Backend Contract

Set this environment variable locally or in Vercel:

```bash
NEXT_PUBLIC_PHARMASIGNAL_API_BASE_URL=https://your-api-id.execute-api.us-east-1.amazonaws.com/prod
```

The frontend currently expects:

```http
GET /dashboard/summary
Accept: application/json
```

The response should match `DashboardData` in `lib/types.ts`, with JSON arrays for:

- `signal_scores`
- `emerging_signals`
- `nhanes_population_context`
- `pubmed_evidence`
- `pipeline_health`
- `data_quality_checks`

This shape maps directly to the existing gold tables used by the Streamlit app.

## Vercel

Use `frontend/` as the Vercel project root.

Build settings:

- Framework preset: Next.js
- Install command: `npm install`
- Build command: `npm run build`
- Output directory: `.next`

Add `NEXT_PUBLIC_PHARMASIGNAL_API_BASE_URL` in Vercel project environment variables
when the AWS API Gateway/Lambda endpoint is ready.

## Verification

```bash
npm run typecheck
npm run build
```
