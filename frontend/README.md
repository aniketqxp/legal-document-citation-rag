# Frontend

React workbench for document upload, multi-document query scope, chat history,
and PDF citation viewing.

## Commands

```bash
npm install
npm run dev
npm run build
```

The development server listens on `http://localhost:5173`.

## API Configuration

Set the backend URL in `frontend/.env`:

```env
VITE_API_URL=http://localhost:8000/api/v1
```

The generated API client lives in `src/client`.
