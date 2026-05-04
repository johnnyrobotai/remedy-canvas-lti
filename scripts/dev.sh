#!/bin/bash
# Start Canvas Remedy LTI local development
# Backend on :8001, Frontend on :5173

echo "Starting Canvas Remedy LTI development servers..."

# Start backend
AUTH_BYPASS_FOR_LOCAL=true uvicorn lti_app.main:app --reload --port 8001 &
BACKEND_PID=$!

# Start frontend
cd frontend && npm run dev &
FRONTEND_PID=$!

echo ""
echo "Backend:  http://localhost:8001"
echo "Frontend: http://localhost:5173"
echo "Health:   http://localhost:8001/health"
echo ""
echo "Press Ctrl+C to stop both servers"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT
wait
