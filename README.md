# crosslinking-api
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A REST API for accessing crosslinking mass spectrometry data from the [PRIDE Crosslinking Resource](https://www.ebi.ac.uk/pride/markdownpage/crosslinking). Built with FastAPI, it serves data parsed by [mzidentml-reader](https://github.com/PRIDE-Archive/mzidentml-reader) and provides endpoints for project search, protein queries, [xiVIEW](https://xiview.org/) visualization, and PDB-IHM integration.

## Requirements
- Python 3.11
- pipenv
- PostgreSQL server
- Redis server (for caching)

## Installation

```bash
git clone https://github.com/PRIDE-Archive/crosslinking-api.git
cd crosslinking-api
pipenv install
pipenv shell
```

## Configuration

Copy the template and fill in your values:

```bash
cp default.database.ini database.ini
```

The `database.ini` file has three sections:

```ini
[postgresql]
host=localhost
database=crosslinking
user=xiadmin
password=your_password_here
port=5432

[security]
apikey=your_api_key
apiversion=v3
apiport=8080
xiviewbaseurl=https://xiview.org/pride

[redis]
host=localhost
port=6379
password=your_redis_password
peptide_per_protein=peptide_per_protein_cache
```

These can also be set via environment variables: `DB_HOST`, `DB_DATABASE_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_PORT`, `API_KEY`, `API_VERSION`, `PORT`, `XIVIEW_PRIDE_URL`, `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`.

## Running

```bash
python main.py
```

The server starts on the port configured in `database.ini` (default: 8080). API docs are available at:

```
http://localhost:{PORT}/pride/ws/archive/crosslinking/{API_VERSION}/docs
```

## API Endpoints

Base path: `/pride/ws/archive/crosslinking/{API_VERSION}`

### Projects

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/projects` | Search projects by ID, title, description, organism, protein accession, protein name, or gene name. Supports pagination. |
| GET | `/projects/{project_id}` | Get project details with protein-level metadata |
| GET | `/projects/{project_id}/proteins` | List proteins in a project with pagination and search |
| GET | `/statistics-count` | Aggregate counts (projects, proteins, peptides, spectra, species) |
| GET | `/projects-per-species` | Project distribution by organism |
| GET | `/peptide-per-protein` | Peptide frequency distribution (Redis-cached) |
| GET | `/health` | Health check with database status |

### xiVIEW Visualization Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/data/visualisations/{project_id}` | List visualization files with xiVIEW URLs |
| GET | `/data/get_peaklist` | Spectrum peak list (m/z, intensity) |
| GET | `/data/get_xiview_matches` | Spectral matches (PSMs with crosslink info) |
| GET | `/data/get_xiview_peptides` | Peptide sequences with modifications |
| GET | `/data/get_xiview_proteins` | Protein sequences and accessions |
| GET | `/data/get_xiview_enzymes` | Enzyme configuration |
| GET | `/data/get_xiview_search_modifications` | Search modification parameters |
| GET | `/data/get_xiview_spectrum_identification_protocols` | Protocol/search configuration |
| GET | `/data/get_xiview_spectra_data` | Spectra data metadata |
| GET | `/data/get_xiview_mzidentml_files` | mzIdentML file metadata |
| GET | `/data/get_xiview_analysis_collection_spectrum_identifications` | Analysis collection data |
| GET | `/data/get_matches_by_multiple_spectra_id` | Matches for a specific spectrum group |
| GET | `/data/get_datasets` | List all projects/files in database |

### PDB-IHM

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/pdbihm/projects/{protein_id}` | Projects containing a protein |
| GET | `/pdbihm/projects/{project_id}/sequences` | Non-decoy protein sequences |
| GET | `/pdbihm/projects/{project_id}/residue-pairs/based-on-reported-psm-level/{passing_threshold}` | Crosslinked residue pairs (`passing` or `all`) |

### Admin (requires API key via `X-API-Key` header)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/parse` | Parse a PXD accession: download mzIdentML files and load into DB |
| POST | `/update-metadata/{project_id}` | Update project metadata from PRIDE API |
| POST | `/update-metadata` | Update metadata for all projects |
| DELETE | `/delete/{project_id}` | Delete a project and all related data |
| PUT | `/log/{level}` | Change logging level dynamically |

### Parser (internal, used by mzidentml-reader)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/parse/write_data` | Bulk insert data into specified table |
| POST | `/parse/write_new_upload` | Create new upload record |
| POST | `/parse/write_mzid_info` | Update upload with mzIdentML metadata |
| POST | `/parse/write_other_info` | Update upload with crosslink and validation info |

## Data Flow

1. **Parse**: `POST /parse` with a PXD accession triggers [mzidentml-reader](https://github.com/PRIDE-Archive/mzidentml-reader) to download and parse mzIdentML files from PRIDE
2. **Store**: Parsed data is written to PostgreSQL via the `/parse/write_*` endpoints (when using `-w api`) or directly to the database (when using `-w db`)
3. **Enrich**: `POST /update-metadata` fetches project metadata, protein names, gene names, and organism info from PRIDE and UniProt APIs
4. **Serve**: Search, visualization, and PDB-IHM endpoints serve the stored data

To parse a dataset using the CLI:
```bash
process_dataset -p PXD038060 --dontdelete -w api
```

Or via the HPC Slurm script:
```bash
./scripts/runDataset.sh -a PXD038060
```

## Deployment

### Docker

```bash
envsubst < .Dockerfile > Dockerfile
docker build -t crosslinking-api:latest .
```

### Kubernetes

The project includes a `.kubernetes.yml` template for deployment on Kubernetes (EBI Embassy Cloud). CI/CD is handled by GitHub Actions workflows (`.github/workflows/`) that build the Docker image, push to GHCR, and deploy to the cluster.

## License

Apache 2.0 - see [LICENSE.md](LICENSE.md)
