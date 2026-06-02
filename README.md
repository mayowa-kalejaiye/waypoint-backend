# Waypoint Backend Documentation

A personalized learning curriculum generation platform that creates adaptive, video-based learning paths tailored to user goals using AI-powered analysis.

## Overview

Waypoint transforms learning by generating customized educational curricula in real-time. Users provide their learning goal, and Waypoint constructs a structured, multi-week curriculum with carefully selected video content, adaptive difficulty progression, and intelligent content sequencing.

### Key Features

- **Intelligent Curriculum Generation**: AI-driven curriculum builder that understands learning goals and maps them to educational content
- **Video-Based Learning Paths**: Curated content from YouTube, ArXiv, GitHub, and documentation
- **Adaptive Learning Progression**: Difficulty levels adjust based on prerequisite mastery
- **Real-Time Content Fetching**: Integrates with multiple content sources (YouTube, ArXiv, GitHub)
- **Intelligent Caching**: Redis-powered caching for optimized performance
- **Rate Limiting**: Built-in protection against abuse with configurable rate limits
- **Transcript Analysis**: Automatic transcript extraction and intelligent content analysis

## Project Structure

```
waypoint/
├── backend/              # FastAPI backend services
│   ├── models/          # SQLModel definitions (Curriculum, Video, LaunchSession)
│   ├── routers/         # API endpoints
│   ├── services/        # Business logic
│   ├── cache/           # Redis caching layer
│   └── db/              # Database configuration
├── frontend/            # Next.js frontend application
│   ├── src/
│   │   ├── app/         # Next.js app router
│   │   ├── components/  # Reusable React components
│   │   ├── hooks/       # Custom React hooks
│   │   └── types/       # TypeScript type definitions
│   └── public/          # Static assets
└── scripts/             # Utility and migration scripts
```

## Technology Stack

### Backend

- **Framework**: FastAPI 0.112.2
- **Language**: Python 3.13
- **Database**: PostgreSQL with SQLModel ORM
- **Cache**: Redis (remote with ACL)
- **LLM Integration**: Groq API (with NVIDIA fallback)
- **HTTP Client**: Requests
- **Server**: Uvicorn

### Frontend

- **Framework**: Next.js 14.2.5
- **Language**: TypeScript
- **Styling**: Tailwind CSS 3.4.10
- **UI Library**: Lucide React (icons)
- **Animation**: Framer Motion
- **State Management**: React Hooks

## Quick Start

### Prerequisites

- Python 3.13+
- Node.js 18+
- PostgreSQL 13+
- Redis 6+

### Backend Setup

1. Navigate to the backend directory:

```bash
cd waypoint/backend
```

1. Create a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

1. Install dependencies:

```bash
pip install -r requirements.txt
```

1. Configure environment variables:

```bash
# Create a .env file with the following:
DATABASE_URL=postgresql://user:password@localhost/waypoint_db
REDIS_URL=redis://:password@host:port
GROQ_API_KEY=your_groq_api_key
NVIDIA_API_KEY=your_nvidia_api_key
CORS_ORIGINS=http://localhost:3000
```

1. Run database migrations:

```bash
python run_migrations.py
```

1. Start the backend server:

```bash
uvicorn main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`

### Frontend Setup

1. Navigate to the frontend directory:

```bash
cd waypoint/frontend
```

1. Install dependencies:

```bash
npm install
```

1. Configure environment variables:

```bash
# Create a .env.local file with:
NEXT_PUBLIC_API_URL=http://localhost:8000
```

1. Start the development server:

```bash
npm run dev
```

The frontend will be available at `http://localhost:3000`

## API Endpoints

### Core Endpoints

- `POST /api/curriculum/generate` - Generate a new curriculum
- `GET /api/curriculum/{curriculum_id}` - Retrieve curriculum details
- `GET /api/curriculum/{curriculum_id}/videos` - Get videos for a curriculum
- `POST /api/feedback` - Submit feedback on learning experience
- `POST /api/interactions` - Track user interactions with content

See [Backend README](./backend/README.md) for detailed API documentation.

## Architecture

### Backend Architecture

The backend follows a layered architecture pattern:

1. **Routers Layer**: HTTP request handling and validation
2. **Services Layer**: Business logic and orchestration
3. **Models Layer**: Data models and database schemas
4. **Database Layer**: PostgreSQL connection and ORM
5. **Cache Layer**: Redis integration for performance

Key services:

- `curriculum_planner.py`: Generates curricula using LLM-powered analysis
- `content_fetcher.py`: Fetches content from YouTube and documentation
- `scoring_engine.py`: Evaluates video relevance and difficulty
- `dependency_graph.py`: Maps learning prerequisites

### Frontend Architecture

The frontend is built with Next.js App Router and follows component-based architecture:

1. **Pages**: Route handlers and page layouts
2. **Components**: Reusable UI components
3. **Hooks**: Custom React hooks for data fetching and state
4. **Types**: TypeScript definitions for type safety
5. **Utils**: Helper functions and utilities

## Performance Considerations

### Optimizations Implemented

- **Intelligent Goal Parser Caching**: Simple goals use regex patterns, complex goals use cached LLM results
- **Batch Database Operations**: N+1 query elimination through batch loading
- **Redis Caching**: 24-hour TTL for curricula, 30-day TTL for dependency graphs
- **Response Streaming**: Support for progressive curriculum generation

### Performance Baselines

- API sense detection: ~1,500ms
- Curriculum generation: 0.7-5 seconds (with caching: <1s)
- Rate limit checks: <10ms

See [PERFORMANCE_OPTIMIZATION.md](./PERFORMANCE_OPTIMIZATION.md) for detailed analysis.

## Security Features

- CORS restrictions with allowlist
- Rate limiting (1 req/10s burst, 6 req/hour quota)
- Security headers (CSP, X-Frame-Options, HSTS)
- Redis ACL authentication
- Input validation and sanitization
- Session isolation

See [SYSTEM_EVALUATION_REPORT.md](./SYSTEM_EVALUATION_REPORT.md) for security audit details.

## Development Guidelines

### Code Style

- Backend: PEP 8 compliant, type-hinted Python code
- Frontend: TypeScript with strict mode enabled

### Testing

Run tests with:

```bash
# Backend
cd backend && python -m pytest

# Frontend
cd frontend && npm run test
```

### Git Workflow

1. Create a feature branch from `main`
2. Make focused, atomic commits
3. Submit a pull request with detailed description
4. Ensure all tests pass before merging

## Database Schema

Key tables:

- `curriculum`: Generated learning paths
- `video`: Video metadata and transcripts
- `launch_session`: User curriculum sessions
- `moat_features`: Transcript intelligence cache
- `performance_score`: Video performance metrics

## Troubleshooting

### Common Issues

**Backend won't start**

- Verify PostgreSQL is running
- Check DATABASE_URL in environment
- Run migrations: `python run_migrations.py`

**Curriculum generation timeout**

- Check Redis connection
- Verify LLM API keys are valid
- Check network connectivity to content sources

**Frontend API errors**

- Verify backend is running on correct port
- Check CORS configuration
- Inspect browser console for detailed errors

## Contributing

We welcome contributions! Please follow these guidelines:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes with clear, descriptive commits
4. Write tests for new functionality
5. Update documentation as needed
6. Submit a pull request

## Documentation

- [Backend Documentation](./backend/README.md)
- [Frontend Documentation](./frontend/README.md)
- [Performance Optimization Report](./PERFORMANCE_OPTIMIZATION.md)
- [System Evaluation Report](./SYSTEM_EVALUATION_REPORT.md)

## Environment Variables

### Backend (.env)

```
DATABASE_URL=postgresql://user:password@localhost/waypoint_db
REDIS_URL=redis://:password@host:port
GROQ_API_KEY=your_api_key
NVIDIA_API_KEY=your_api_key
CORS_ORIGINS=http://localhost:3000,http://localhost:8000
GEN_BURST_LIMIT_COUNT=1
GEN_HOURLY_LIMIT_COUNT=6
```

### Frontend (.env.local)

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## License

This project is open source and available under the MIT License. See LICENSE file for details.

## Support

For questions, issues, or feature requests, please open an issue on GitHub or contact the development team.

## Authors

Created as a personalized learning platform to transform educational experiences through AI-powered curriculum generation.

---

For detailed technical documentation, see the [Backend](./backend/README.md) and [Frontend](./frontend/README.md) READMEs.
