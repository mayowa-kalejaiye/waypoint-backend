# Waypoint Backend

The backend is a FastAPI-based service that powers curriculum generation, content discovery, and user interaction tracking. It integrates with multiple LLM providers, content sources, and caching mechanisms to deliver personalized learning paths.

## Overview

The backend is responsible for:
- AI-powered curriculum generation using LLM analysis
- Content discovery from multiple sources (YouTube, ArXiv, GitHub)
- User session management and tracking
- Performance caching with Redis
- Rate limiting and quota enforcement
- Transcript analysis and content intelligence

## Architecture

### Layered Architecture

```
┌─────────────────────────────────────────────┐
│          HTTP Layer (FastAPI)               │
│          Routers (API Endpoints)            │
├─────────────────────────────────────────────┤
│        Business Logic (Services)            │
│  - curriculum_planner                       │
│  - scoring_engine                           │
│  - content_fetcher                          │
│  - goal_parser                              │
├─────────────────────────────────────────────┤
│        Data Layer (Models & ORM)            │
│  - SQLModel for type-safe database ops      │
│  - Pydantic for request/response validation │
├─────────────────────────────────────────────┤
│   Infrastructure (Cache, Auth, Logging)     │
│  - Redis caching layer                      │
│  - Rate limiting                            │
│  - Session management                       │
└─────────────────────────────────────────────┘
```

### Key Directories

```
backend/
├── main.py                    # FastAPI application entry point
├── config.py                  # Configuration management
├── requirements.txt           # Python dependencies
├── run_migrations.py          # Database migration script
│
├── models/
│   ├── curriculum.py         # Curriculum data model
│   ├── video.py              # Video metadata model
│   ├── launch.py             # User session model
│   └── moat_features.py       # Transcript intelligence model
│
├── routers/
│   ├── curriculum.py         # Curriculum generation endpoints
│   ├── feedback.py           # User feedback endpoints
│   ├── interactions.py        # User interaction tracking
│   └── launch.py             # Session management endpoints
│
├── services/
│   ├── curriculum_planner.py  # Curriculum generation logic
│   ├── content_fetcher.py     # Multi-source content discovery
│   ├── scoring_engine.py      # Video relevance scoring
│   ├── llm_client.py          # LLM API integration
│   ├── groq_client.py         # Groq-specific integration
│   ├── goal_parser.py         # Goal analysis and parsing
│   ├── dependency_graph.py    # Learning prerequisite mapping
│   ├── transcript_analyzer.py # Video transcript analysis
│   ├── embeddings.py          # Vector embeddings
│   ├── domain_classifier.py   # Content domain classification
│   ├── youtube_fetcher.py     # YouTube content discovery
│   ├── github_fetcher.py      # GitHub repository discovery
│   ├── arxiv_fetcher.py       # ArXiv paper discovery
│   ├── docs_fetcher.py        # Documentation fetching
│   ├── analytics.py           # Analytics and metrics
│   └── diversity_validator.py # Content diversity checking
│
├── cache/
│   └── redis_client.py        # Redis connection and operations
│
└── db/
    └── database.py            # PostgreSQL configuration
```

## Getting Started

### Prerequisites
- Python 3.13+
- PostgreSQL 13+
- Redis 6+
- API Keys: Groq or NVIDIA for LLM access

### Installation

1. Create and activate virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure environment:
```bash
# Copy and edit the configuration
cp .env.example .env  # Create if needed
```

4. Set up environment variables:
```bash
# Required
DATABASE_URL=postgresql://user:password@localhost:5432/waypoint_db
REDIS_URL=redis://user:password@localhost:6379
GROQ_API_KEY=your_groq_api_key

# Optional
NVIDIA_API_KEY=your_nvidia_api_key
CORS_ORIGINS=http://localhost:3000
```

5. Initialize the database:
```bash
python run_migrations.py
```

6. Start the server:
```bash
uvicorn main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`
API documentation (Swagger UI) at `http://localhost:8000/docs`

## Development Environment (Windows)

For Windows development using SQLite instead of PostgreSQL:

### Quick Setup
1. **Virtual Environment**:
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

2. **Install Dependencies**:
   ```powershell
   pip install -r requirements.txt
   ```

3. **Environment File** (`.env`):
   ```bash
   # SQLite for development (auto-created)
   DATABASE_URL=sqlite:///waypoint.sqlite3
   
   # Optional Redis (can use in-memory fallback)
   REDIS_URL=redis://localhost:6379
   
   # API Keys
   GROQ_API_KEY=your_key_here
   ```

4. **Run Server**:
   ```powershell
   uvicorn main:app --reload --port 8000
   ```

### Notes
- SQLite is automatically created on first run
- Redis is optional; in-memory rate limiting activates if Redis unavailable
- Migrations auto-run on startup
- Check `analytics.log` for tracking events
- Database file: `waypoint.sqlite3` (tracked in .gitignore)

## API Endpoints

### Curriculum Management

#### Generate Curriculum
```
POST /api/v1/curriculum/generate
Content-Type: application/json

{
  "goal": "Learn Python for data science",
  "level": "beginner"
}

Response: {
  "curriculum_id": "uuid",
  "status": "generating",
  "goal": "Learn Python for data science",
  "level": "beginner",
  "weeks": 4,
  "created_at": "2024-05-23T10:00:00Z"
}
```

Status codes:
- 200: Curriculum generated successfully
- 400: Invalid input
- 429: Rate limit exceeded
- 500: Server error

#### Retrieve Curriculum
```
GET /api/v1/curriculum/{curriculum_id}

Response: {
  "id": "uuid",
  "goal": "Learn Python for data science",
  "level": "beginner",
  "topic": "Python Data Science",
  "weeks": [
    {
      "week_number": 1,
      "days": [...]
    }
  ]
}
```

#### Get Curriculum Videos
```
GET /api/v1/curriculum/{curriculum_id}/videos?week=1&day=1

Response: {
  "videos": [
    {
      "id": "video_id",
      "title": "Python Basics",
      "source": "youtube",
      "duration": 1200,
      "url": "...",
      "difficulty": 0.2
    }
  ]
}
```

#### Export Curriculum as Playlists
```
GET /api/v1/curriculum/{curriculum_id}/playlists

Response: {
  "curriculum_id": "uuid",
  "topic": "Python Data Science",
  "level": "beginner",
  "videos": [
    {
      "id": "video_id",
      "title": "Python Basics",
      "duration": 1200,
      "url": "https://youtube.com/watch?v=..."
    }
  ],
  "by_week": [
    {
      "week": 1,
      "videos": [...]
    }
  ],
  "youtube_import_url": "https://youtube.com/playlist_import_url"
}
```

### Feedback and Interactions

#### Submit Feedback
```
POST /api/v1/feedback
Content-Type: application/json

{
  "curriculum_id": "uuid",
  "video_id": "video_id",
  "rating": 4,
  "helpful": true,
  "comments": "Very clear explanation"
}
```

#### Track Interactions
```
POST /api/v1/interactions
Content-Type: application/json

{
  "session_id": "uuid",
  "event_type": "video_watched",
  "metadata": {
    "video_id": "video_id",
    "duration_watched": 600,
    "completion_percentage": 85
  }
}
```

#### Track Launch Events
Analytics tracking for curriculum generation and user actions:

```
POST /api/v1/analytics/events
Content-Type: application/json

{
  "event_type": "curriculum_generated",
  "curriculum_id": "uuid",
  "goal": "Learn Python for data science",
  "level": "beginner"
}
```

Tracked events:
- `curriculum_generated`: Logged when curriculum is successfully created
- `curriculum_viewed`: User opens a curriculum
- `video_watched`: User completes or watches video
- `feedback_submitted`: User rates or comments on content

## Configuration

Configuration is managed through environment variables and `config.py`:

### Database Configuration
- `DATABASE_URL`: PostgreSQL connection string
- Database is automatically created if not exists
- Migrations run on startup

### Redis Configuration
- `REDIS_URL`: Redis connection string with optional password
- `CACHE_TTL_CURRICULUM`: How long to cache curricula (default: 24 hours)
- `CACHE_TTL_GRAPHS`: How long to cache dependency graphs (default: 30 days)

### LLM Configuration
- `GROQ_API_KEY`: Primary LLM provider
- `NVIDIA_API_KEY`: Fallback LLM provider
- `LLM_MODEL`: Model to use (default: groq-llama)
- `LLM_MAX_TOKENS`: Maximum tokens for generation (default: 6500)

### Rate Limiting
- `GEN_BURST_LIMIT_COUNT`: Requests per burst window (default: 1)
- `GEN_BURST_LIMIT_WINDOW_SECS`: Burst window in seconds (default: 10)
- `GEN_HOURLY_LIMIT_COUNT`: Requests per hour (default: 6)

### CORS Configuration
- `CORS_ORIGINS`: Comma-separated list of allowed origins
- `ALLOW_CREDENTIALS`: Allow cookies in CORS requests (default: true)

## Database Models

### Curriculum
Represents a generated learning path.

```python
class Curriculum(SQLModel, table=True):
    id: str = Field(default_factory=uuid4)
    user_id: str
    goal: str
    level: str  # beginner, intermediate, advanced
    status: str  # generating, completed, failed
    weeks: int
    estimated_hours: float
    created_at: datetime
    updated_at: datetime
    data: dict  # Full curriculum structure
```

### Video
Video metadata and transcripts.

```python
class Video(SQLModel, table=True):
    id: str = Field(default_factory=uuid4)
    youtube_id: str
    title: str
    source: str  # youtube, arxiv, github, docs
    duration: int
    url: str
    channel: str
    transcript: Optional[str]
    created_at: datetime
```

### LaunchSession
User curriculum sessions.

```python
class LaunchSession(SQLModel, table=True):
    id: str = Field(default_factory=uuid4)
    curriculum_id: str = Field(foreign_key="curriculum.id")
    user_id: str
    started_at: datetime
    completed_at: Optional[datetime]
    current_week: int
    current_day: int
```

### MOATFeatures
Transcript intelligence and analysis cache.

```python
class MOATFeatures(SQLModel, table=True):
    id: str = Field(default_factory=uuid4)
    youtube_id: str = Field(foreign_key="video.youtube_id")
    key_concepts: list[str]
    difficulty_score: float
    domain: str
    quality_metrics: dict
    cached_at: datetime
```

## Services

### Curriculum Planner
Orchestrates curriculum generation using LLM analysis.

```python
async def build_curriculum(goal: str, level: str) -> dict:
    """
    Generate a comprehensive curriculum for the given goal.
    
    Process:
    1. Parse user goal using goal parser
    2. Build dependency graph for concepts
    3. Score videos for each concept
    4. Assemble into weekly structure
    5. Cache result for future use
    """
```

### Content Fetcher
Discovers and fetches content from multiple sources.

```python
async def fetch_content(concept: str, count: int = 50) -> List[Video]:
    """
    Fetch content for a concept from all available sources.
    
    Sources (in priority order):
    1. YouTube (primary)
    2. GitHub repositories
    3. ArXiv papers
    4. Documentation sites
    """
```

### Scoring Engine
Evaluates video relevance and difficulty with strong preference for long-form content.

```python
async def score_video(video: Video, concept: str, level: str) -> float:
    """
    Score a video for relevance to a concept at a specific level.
    
    Base Factors:
    - Keyword relevance (title, transcript)
    - Video quality (duration, channel authority)
    - Difficulty alignment
    - Content recency
    - Engagement metrics
    
    Long-Form Video Boost (duration-based):
    - 60+ minutes: +28 points (strong preference)
    - 45-59 minutes: +18 points (moderate boost)
    - 30-44 minutes: +10 points (light boost)
    - <30 minutes: 0 points (no boost)
    
    Rationale: Long-form content provides comprehensive coverage
    and reduces cognitive load from context switching between
    multiple short videos.
    """
```

### Goal Parser
Analyzes user goals using intelligent caching.

```python
async def parse_goal(goal: str) -> ParsedGoal:
    """
    Parse a user goal into structured components.
    
    Uses:
    - Fast regex patterns for simple goals
    - LLM analysis for complex goals
    - In-memory cache with 1-hour TTL
    
    Returns domain, subdomains, difficulty, estimated hours
    """
```

### Dependency Graph
Maps learning prerequisites.

```python
async def build_dependency_graph(domain: str, concepts: List[str]) -> Graph:
    """
    Build a directed graph of concept prerequisites.
    
    Uses:
    - LLM for complex domains
    - Template-based fallback for common domains
    - 30-day cache TTL
    """
```

## Caching Strategy

### Cache Layers

1. **In-Memory Cache** (Python dict)
   - Goal parser results (1-hour TTL)
   - Rate limiting counters (per-second, per-hour)
   - Session data (temporary)

2. **Redis Cache** (distributed)
   - Curricula (24-hour TTL)
   - Dependency graphs (30-day TTL)
   - Video scoring results (7-day TTL)
   - Transcript intelligence (permanent)

3. **Database Cache**
   - Video metadata
   - User interactions
   - Performance metrics

### Cache Invalidation

- Curricula: Invalidated after 24 hours or on user request
- Graphs: Invalidated after 30 days
- Transcripts: Never invalidated (immutable)
- Scores: Invalidated after 7 days

## Rate Limiting

Rate limiting protects against abuse with two thresholds:

### Burst Limit
- 1 request per 10 seconds (configurable)
- Applied per session ID
- Returns 429 with "Retry-After" header

### Hourly Limit
- 6 requests per hour (configurable)
- Separate quota enforcement
- Resets hourly

### Implementation
- Primary: Redis-based distributed tracking
- Fallback: In-memory threading.Lock (if Redis unavailable)

Example response when rate limited:
```json
{
  "detail": "Rate limit exceeded. Please wait 7 seconds before trying again."
}
```

## Recent Features

### Long-Form Video Preference
The scoring engine now strongly prefers long-form videos (60+ minutes) to provide comprehensive learning with minimal context switching.

- **60+ minutes**: +28 points boost
- **45-59 minutes**: +18 points boost
- **30-44 minutes**: +10 points boost

This preference is integrated into all curriculum recommendations and ensures learners get deep, focused content.

### Playlist Export
Curricula can now be exported as playlists for easy YouTube import or external sharing.

- Endpoint: `GET /api/v1/curriculum/{curriculum_id}/playlists`
- Returns structured playlist with all videos and weekly organization
- Includes pre-generated YouTube import URL for one-click playlist creation

### Analytics Tracking
All curriculum generation and user interactions are tracked for usage metrics.

- Logs to `analytics.log` JSONL format
- Stores events in SQLite `launch_analytics_events` table
- Tracks: curriculum generation, user views, video engagement
- Supports filtering by curriculum_id and event_type

## Error Handling

All endpoints follow consistent error response format:

```json
{
  "detail": "Human-readable error message",
  "error_code": "specific_error_code",
  "timestamp": "2024-05-23T10:00:00Z"
}
```

Common status codes:
- 200: Success
- 201: Created
- 400: Bad request (invalid input)
- 401: Unauthorized
- 404: Not found
- 429: Rate limit exceeded
- 500: Server error
- 503: Service unavailable

## Logging

Comprehensive logging for debugging and monitoring:

```python
# Request/response logging
logger.info(f"Curriculum generation started for goal: {goal}")
logger.debug(f"Using {n_videos} videos for scoring")

# Error logging with context
logger.error(f"Failed to fetch videos: {error}", exc_info=True)

# Performance logging
logger.info(f"Curriculum generated in {elapsed_time:.2f}s")
```

Access logs are available in console output when running with `--log-level info`

## Testing

### Unit Tests
```bash
pytest tests/unit/ -v
```

### Integration Tests
```bash
pytest tests/integration/ -v
```

### End-to-End Tests
```bash
pytest tests/e2e/ -v
```

Test database is automatically created from migrations.

## Performance Optimization

### Key Optimizations

1. **Goal Parser Caching**
   - Simple goals use fast regex patterns
   - Complex goals cached after first LLM call
   - 66% reduction in goal parsing time

2. **Batch Database Operations**
   - Load related records in single query
   - N+1 query elimination
   - In-memory dictionary lookups

3. **Redis Caching**
   - 24-hour curriculum TTL
   - 30-day dependency graph TTL
   - Reduces generation time from 5s to <1s on cache hit

4. **Content Batching**
   - Fetch 50+ videos per concept
   - Batch scoring operations
   - Parallel content source queries

### Performance Baselines

- Goal parsing: 0s (cached) to 1.5s (LLM)
- Curriculum generation: 0.7-5s (first run)
- Cached retrieval: <100ms
- Rate limit check: <10ms

See [PERFORMANCE_OPTIMIZATION.md](../PERFORMANCE_OPTIMIZATION.md) for detailed analysis.

## Security

### Authentication
Currently session-ID based. For production, consider:
- JWT tokens
- OAuth 2.0 integration
- API key management

### Authorization
- Rate limits enforced per session
- User data isolation per curriculum

### Security Headers
```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Cross-Origin-Resource-Policy: same-site
Content-Security-Policy: default-src 'none'
```

### Input Validation
- Pydantic models validate all inputs
- String length limits enforced
- Enum validation for fields like difficulty level

### Database Security
- Parameterized queries (SQLModel)
- No raw SQL in application code
- Connection pooling with SSL support

## Deployment

### Docker

Build the Docker image:
```bash
docker build -t waypoint-backend:latest .
```

Run the container:
```bash
docker run -e DATABASE_URL=... -e REDIS_URL=... -p 8000:8000 waypoint-backend:latest
```

### Environment Variables for Production

```bash
# Required
DATABASE_URL=postgresql://user:password@prod-db:5432/waypoint
REDIS_URL=redis://:password@prod-redis:6379
GROQ_API_KEY=prod_key

# Recommended
ENVIRONMENT=production
LOG_LEVEL=info
CORS_ORIGINS=https://yourapp.com,https://www.yourapp.com
```

### Health Checks

The backend provides health check endpoints:

```
GET /health
Returns: {"status": "ok", "timestamp": "2024-05-23T10:00:00Z"}

GET /api/health
Returns: {"database": "ok", "redis": "ok", "status": "ok"}
```

## Troubleshooting

### Database Connection Error
```
Error: could not connect to server: Connection refused
```
- Verify PostgreSQL is running
- Check DATABASE_URL format
- Test connection: `psql postgresql://user:password@localhost:5432/waypoint`

### Redis Connection Error
```
Error: Cannot connect to Redis
```
- Verify Redis is running on configured port
- Check REDIS_URL includes credentials if needed
- Test: `redis-cli -u redis://host:port`

### LLM API Error
```
Error: Groq API call failed
```
- Verify GROQ_API_KEY is set correctly
- Check API rate limits on Groq dashboard
- Fallback to NVIDIA_API_KEY if available

### Curriculum Generation Timeout
- Check if Redis is working (for caching)
- Monitor database query performance
- Verify network connectivity to content sources
- Increase LLM timeout if needed

### Rate Limit Issues
Check Redis connection first:
```bash
redis-cli -u $REDIS_URL ping
```
If Redis down, fallback to in-memory limiting will activate.

## Contributing

### Development Setup
1. Create feature branch: `git checkout -b feature/amazing-feature`
2. Follow PEP 8 style guide
3. Add type hints to all functions
4. Write tests for new functionality
5. Update documentation

### Code Standards
- Type hints required for all functions
- Docstrings for public functions and classes
- Maximum line length: 100 characters
- Use f-strings for string formatting

### Pull Request Process
1. Ensure tests pass: `pytest`
2. Run linter: `pylint backend/`
3. Update CHANGELOG
4. Create PR with detailed description

## Additional Resources

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [SQLModel Documentation](https://sqlmodel.tiangolo.com/)
- [Redis Documentation](https://redis.io/documentation)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)

## Support

For questions or issues, please open an issue on GitHub or contact the development team.

---

See the [main README](../README.md) for project overview and the [Frontend README](../frontend/README.md) for frontend documentation.
