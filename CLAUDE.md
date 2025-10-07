# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Application Overview

This is a FastAPI-based web API for the PRIDE Crosslinking Section, providing REST endpoints for managing and accessing crosslinking proteomics data. The API serves scientific data to external consumers and visualization tools.

## Common Development Commands

### Environment Setup
```bash
# Install dependencies using pipenv
pipenv install

# Activate virtual environment
pipenv shell

# Install development dependencies
pipenv install --dev
```

### Running the Application
```bash
# Start the development server
python main.py

# The server will start on the port specified in database.ini (default: 8080)
# Access API docs at: http://localhost:{port}/pride/ws/archive/crosslinking/v2/docs
```

### Testing
```bash
# Run tests using pytest
pytest
```

## Architecture and Structure

### Core Components
- **FastAPI Application**: Main app defined in `app/api.py` with middleware for CORS, GZip compression, and request timing
- **Database Layer**: PostgreSQL with SQLAlchemy ORM, Redis caching for performance
- **Route Organization**: Modular router structure in `app/routes/`
  - `pride.py`: Core PRIDE functionality
  - `pdbdev.py`: PDB-Dev/IHM protein structure endpoints
  - `xiview.py`: Visualization data endpoints
  - `parse.py`: Data parsing and processing
  - `shared.py`: Common utilities

### Configuration Management
- **Database Config**: Environment variables with INI file fallbacks via `db_config_parser.py`
- **Logging**: Configured through `logging.ini`
- **Settings**: Key configuration in `database.ini` (auto-generated from `default.database.ini`)

### Key Data Models
External models imported from separate package covering:
- Project metadata (`ProjectDetail`, `ProjectSubDetail`)
- Mass spectrometry data (`Spectrum`, `SpectraData`)
- Peptide information (`ModifiedPeptide`, `PeptideEvidence`)
- Database sequences and analysis results

### API Versioning
- Base URL: `/pride/ws/archive/crosslinking/v2/`
- Version controlled through `API_VERSION` environment variable
- OpenAPI documentation auto-generated

## Deployment Pipeline

### Local Development
- Uses Pipenv for dependency management (Python 3.10)
- Configuration through environment variables or INI files
- Redis required for caching functionality

### Production Deployment
- **Containerization**: Docker multi-stage build (`.Dockerfile` -> `Dockerfile`)
- **Orchestration**: Kubernetes on EBI Embassy Cloud OpenStack
- **Registry**: GitHub Container Registry (GHCR)
- **CI/CD**: GitHub Actions triggers on `pride` branch pushes
- **Environment**: ConfigMaps for database and logging configuration

### Branch Strategy
- `main`: Main development branch
- `pride`: Production deployment branch (triggers CI/CD)
- `dev`: Current working branch

## Database Integration

### Connection Management
- PostgreSQL primary database ("xiview")
- Redis for caching with hiredis for performance
- Session management through `index.py`
- Async support via asyncpg

### Data Processing
- Scientific data handling with numpy, pandas, pyteomics
- Mass spectrometry file parsing (pymzml, mzidentml-reader)
- Crosslinking data visualization support

## Security and Performance
- API key authentication via `python-jose`
- ORJSON for optimized JSON responses
- GZip compression for responses >1KB
- Request timing middleware with comprehensive logging
- CORS configured for cross-origin requests