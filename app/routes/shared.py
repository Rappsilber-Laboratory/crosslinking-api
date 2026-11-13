import asyncio
import asyncpg
import functools
import json
import logging
import re
import os
import traceback
import orjson
import logging.config
import time

# import redis  # Redis caching disabled
from configparser import ConfigParser
from fastapi import status, HTTPException, Security, Response
from fastapi.responses import ORJSONResponse
from fastapi.security import APIKeyHeader
from typing import Optional, List, Any

from db_config_parser import security_API_key, redis_config as get_redis_config

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Global asyncpg connection pool
_db_pool: Optional[asyncpg.Pool] = None


async def init_db_pool():
    """Initialize the asyncpg connection pool. Call at app startup."""
    global _db_pool
    if _db_pool is not None:
        return
    db_info = await _parse_database_info()
    _db_pool = await asyncpg.create_pool(
        min_size=10,
        max_size=30,
        statement_cache_size=0,
        **db_info,
    )
    logger.info("asyncpg connection pool initialized (min=10, max=30)")


async def close_db_pool():
    """Close the asyncpg connection pool. Call at app shutdown."""
    global _db_pool
    if _db_pool is not None:
        await _db_pool.close()
        _db_pool = None
        logger.info("asyncpg connection pool closed")


async def _parse_database_info():
    config = os.environ.get('DB_CONFIG', 'database.ini')
    parser = ConfigParser()
    parser.read(config)
    section = 'postgresql'
    db = {}
    if parser.has_section(section):
        params = parser.items(section)
        for param in params:
            db[param[0]] = param[1]
    else:
        raise Exception(f'Section {section} not found in the {config} file')
    return db


# ---------------------------------------------------------------------------
# Redis caching DISABLED. All Redis code below is commented out; the cache
# helpers are no-ops so endpoints always compute fresh.
# ---------------------------------------------------------------------------
# Shared Redis connection pool (lazy-initialized)
# _redis_pool: Optional[redis.ConnectionPool] = None
# _redis_cluster_client = None
_XIVIEW_CACHE_TTL = 43200  # 12 hours in seconds (kept for signature compatibility)


# def _is_redis_cluster(redis_cfg) -> bool:
#     """True when the [redis] config enables cluster mode."""
#     return str(redis_cfg.get('cluster', '')).strip().lower() in ('1', 'true', 'yes', 'on')


# def build_redis_client(redis_cfg, decode_responses: bool = False):
#     """Build a Redis client from a config dict.
#
#     When `cluster=true` is set in the [redis] config section, returns a
#     RedisCluster client which follows MOVED/ASK slot redirects. Otherwise
#     returns a standalone client. The standalone client raises MovedError
#     (string "<slot> <host>:<port>") against a clustered Redis.
#     """
#     host = redis_cfg['host']
#     port = int(redis_cfg['port'])
#     password = redis_cfg.get('password') or None
#     if _is_redis_cluster(redis_cfg):
#         from redis.cluster import RedisCluster
#         return RedisCluster(host=host, port=port, password=password,
#                             decode_responses=decode_responses)
#     return redis.Redis(host=host, port=port, password=password,
#                        decode_responses=decode_responses)


# def _get_redis_client():
#     """Get a shared Redis client (cluster-aware), or None if unavailable."""
#     global _redis_pool, _redis_cluster_client
#     try:
#         redis_cfg = get_redis_config()
#         if _is_redis_cluster(redis_cfg):
#             if _redis_cluster_client is None:
#                 from redis.cluster import RedisCluster
#                 _redis_cluster_client = RedisCluster(
#                     host=redis_cfg['host'],
#                     port=int(redis_cfg['port']),
#                     password=redis_cfg.get('password') or None,
#                     decode_responses=False,
#                 )
#             return _redis_cluster_client
#         if _redis_pool is None:
#             _redis_pool = redis.ConnectionPool(
#                 host=redis_cfg['host'],
#                 port=int(redis_cfg['port']),
#                 password=redis_cfg.get('password'),
#                 max_connections=20,
#                 decode_responses=False,
#             )
#         return redis.Redis(connection_pool=_redis_pool)
#     except Exception as e:
#         logger.warning(f"Redis unavailable, skipping cache: {e}")
#         return None


def get_cached_response(cache_key: str) -> Optional[bytes]:
    """Redis disabled — always a cache miss."""
    return None
    # client = _get_redis_client()
    # if client is None:
    #     return None
    # try:
    #     data = client.get(cache_key)
    #     if data:
    #         logger.info(f"Cache HIT for {cache_key}")
    #     return data
    # except Exception as e:
    #     logger.warning(f"Redis get failed for {cache_key}: {e}")
    #     return None


def set_cached_response(cache_key: str, data: bytes, ttl: int = _XIVIEW_CACHE_TTL):
    """Redis disabled — no-op."""
    return
    # client = _get_redis_client()
    # if client is None:
    #     return
    # try:
    #     client.setex(cache_key, ttl, data)
    #     logger.info(f"Cache SET for {cache_key} (TTL={ttl}s)")
    # except Exception as e:
    #     logger.warning(f"Redis set failed for {cache_key}: {e}")


def build_xiview_cache_key(endpoint: str, project: str, file: Optional[str] = None) -> str:
    """Build a consistent cache key for xiVIEW endpoints."""
    if file:
        return f"xiview:{endpoint}:{project}:{file}"
    return f"xiview:{endpoint}:{project}"


class KeysetPage:
    """
    SQL fragments for fetching one page of a query ordered by a unique key (keyset pagination).

    Pages follow each other by passing the key of the last row of one page as `after` for the next,
    which (unlike OFFSET) gives disjoint pages and doesn't make the database produce and skip the
    earlier rows. OFFSET is still supported for existing API users. Any page request (limit given)
    is ordered by the key, so pages are stable between requests.

    Constructing one appends the values for its placeholders to `params`. Then:
    - filter() gives the "key > after" condition, for the key columns or, with the same placeholders,
      for other columns holding the same values: in a query that joins CTEs, filtering each CTE by the
      cursor too keeps Postgres from building the whole result for every page (on DUC-XL-MS peptides,
      pages got slower the further in they were: 3s for the first, 150s by the sixth);
    - order_limit goes at the end of the query.
    Both are "" when there's nothing to add.
    """

    def __init__(self, key_columns: List[str], after: Optional[List[Any]], limit: Optional[int],
                 offset: int, params: List[Any]):
        """
        :param key_columns: SQL expressions that together uniquely identify a row, e.g. ["si.upload_id", "si.id"]
        :param after: key values of the last row of the previous page (one per key column), or None for the first page
        :param limit: max rows per page, or None for no paging (then after must be None and offset 0)
        :param offset: rows to skip (legacy paging), 0 for none
        :param params: query params so far; placeholders are numbered after them
        """
        self.key_columns = key_columns
        self.placeholders = None
        self.order_limit = ""
        if limit is None:
            if after is not None or offset:
                raise HTTPException(status_code=422, detail="after_* and offset need limit")
            return
        if after is not None and offset:
            raise HTTPException(status_code=422, detail="use either after_* or offset, not both")
        if after is not None:
            if len(after) != len(key_columns) or any(v is None for v in after):
                raise HTTPException(status_code=422, detail="all after_* params are needed together")
            first = len(params) + 1
            self.placeholders = ", ".join(f"${first + i}" for i in range(len(after)))
            params.extend(after)
        self.order_limit = f" ORDER BY {', '.join(key_columns)} LIMIT ${len(params) + 1}"
        params.append(limit)
        if offset:
            self.order_limit += f" OFFSET ${len(params) + 1}"
            params.append(offset)

    def filter(self, columns: Optional[List[str]] = None, conjunction: str = "AND") -> str:
        """
        " <conjunction> (columns) > (after values)", or "" on a first page / without paging.
        :param columns: columns to compare, default the key columns
        :param conjunction: keyword joining it to the query ("AND", or "WHERE" if there's no WHERE yet)
        """
        if self.placeholders is None:
            return ""
        return f" {conjunction} ({', '.join(columns or self.key_columns)}) > ({self.placeholders})"


def any_after(*values) -> Optional[List[Any]]:
    """The after_* query params as a list, or None if none of them were given."""
    return list(values) if any(v is not None for v in values) else None


def log_execution_time_async(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        result = await func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        logging.info(f'"{func.__name__}" executed in {execution_time:.2f} seconds')
        return result
    return wrapper


def log_json_size(json_bytes, name):
    json_size_mb = len(json_bytes) / (1024 * 1024)
    logger.info(f"uncompressed size of json {name}: {json_size_mb} Mb")

# @log_execution_time_async
async def get_most_recent_upload_ids(pxid, file=None):

    """
    Get the most recent upload ids for a project/file.

    :param pxid: identifier of a project,
        for ProteomeXchange projects this is the PXD****** accession
    :param file: name of the file
    :return: upload ids
    """
    if file:
        filename_clean = re.sub(r'[^0-9a-zA-Z-]+', '-', file)
        query = """SELECT id FROM upload 
                WHERE project_id = $1 AND identification_file_name_clean = $2
                ORDER BY upload_time DESC LIMIT 1;"""
        upload_ids = await execute_query(query, [pxid, filename_clean], fetch_one=True)
    else:
        query = """SELECT u.id
                    FROM upload u
                    where u.upload_time = 
                        (select max(upload_time) from upload 
                        where project_id = u.project_id 
                        and identification_file_name = u.identification_file_name )
                    and u.project_id = $1;"""
        upload_ids = await execute_query(query, [pxid])
    if upload_ids is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No upload found for the given project/file")
    return upload_ids


async def get_db_connection():
    """Get a connection from the pool. Prefer using execute_query() instead."""
    if _db_pool is None:
        await init_db_pool()
    conn = await _db_pool.acquire()
    await conn.set_type_codec(
        'json',
        encoder=json.dumps,
        decoder=json.loads,
        schema='pg_catalog'
    )
    return conn


def get_api_key(key: str = Security(api_key_header)) -> str:

    if key == security_API_key():
        return key
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API Key",
    )


async def execute_query(query: str, params: Optional[List[Any]] = None, fetch_one: bool = False,
                        no_nested_loops: bool = False):
    """
    Execute a query and return the result
    :param query: the query to execute
    :param params: the parameters to pass to the query
    :param fetch_one: whether to fetch one result or all
    :param no_nested_loops: run with enable_nestloop off (for this query only). For paged queries whose
        row estimates go wrong: with stale statistics Postgres can estimate 1 row for a page's cursor
        range and pick a nested loop that reruns a big join for every row (seen on DUC-XL-MS peptides:
        a 2s page never finished)
    :return: the result of the query
    """
    if _db_pool is None:
        await init_db_pool()
    try:
        async with _db_pool.acquire() as conn:
            await conn.set_type_codec(
                'json',
                encoder=json.dumps,
                decoder=json.loads,
                schema='pg_catalog'
            )
            fetch = conn.fetchrow if fetch_one else conn.fetch
            if no_nested_loops:
                async with conn.transaction():  # SET LOCAL lasts until the end of the transaction
                    await conn.execute("SET LOCAL enable_nestloop = off")
                    return await fetch(query, *(params or []))
            return await fetch(query, *(params or []))

    except asyncio.TimeoutError:
        logger.error(
            "DB pool acquire timed out (pool exhausted) — all 30 connections busy\nCaller stack:\n%s",
            ''.join(traceback.format_stack()[:-1])
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database busy")
    except Exception:
        logging.exception(
            "Database operation failed\nCaller stack:\n%s",
            ''.join(traceback.format_stack()[:-1])
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database operation failed")

async def fetch_json_response(query, params):
    """
    Fetch a JSON response from the database
    :param query: the query to execute
    :param params: the parameters to pass to the query
    :return: the JSON response
    """
    records = await execute_query(query, params)
    # default=str so non-native types (e.g. asyncpg UUID) serialize instead of raising
    return Response(
        orjson.dumps([dict(r) for r in records], default=str),
        media_type='application/json',
    )
