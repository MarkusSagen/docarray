import sqlite3
import warnings
from dataclasses import dataclass, field
from typing import Dict, Optional, TYPE_CHECKING, Union, Tuple

from ..base.backend import BaseBackendMixin
from ....helper import dataclass_from_dict

from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk

if TYPE_CHECKING:
    from ....types import (
        DocumentArraySourceType,
    )


def _sanitize_table_name(table_name: str) -> str:
    ret = ''.join(c for c in table_name if c.isalnum() or c == '_')
    if ret != table_name:
        warnings.warn(f'The table name is changed to {ret} due to illegal characters')
    return ret


@dataclass
class ElasticConfig:
    n_dim: int  # dims  in elastic
    host: Optional[str] = field(default='http://localhost')
    port: Optional[int] = field(default=9200)
    distance: str = 'cosine'  # similarity in elastic
    index_name: Optional[str] = field(default='index_name')
    ca_certs: str = 'http_ca.crt'
    basic_auth: Optional[str, str] = field(default=(None, None))
    serialize_config: Dict = field(default_factory=dict)


class BackendMixin(BaseBackendMixin):
    """Provide necessary functions to enable this storage backend."""

    def _init_storage(
        self,
        _docs: Optional['DocumentArraySourceType'] = None,
        config: Optional[Union[ElasticConfig, Dict]] = None,
        **kwargs,
    ):

        if not config:
            raise ValueError('Empty config is not allowed for Elastic storage')
        elif isinstance(config, dict):
            config = dataclass_from_dict(ElasticConfig, config)

        self._config = config
        self._client = self._build_client(self._config)
        self._index_name_offset2id = 'index_offset2id'

    def _build_hosts(self, elastic_config):
        return elastic_config.host + ':' + str(elastic_config.port)

    def _build_schema_from_elastic_config(self, elastic_config):
        da_schema = {
            "mappings": {
                "dynamic": "true",
                "_source": {"enabled": "true"},
                "properties": {
                    "embedding": {
                        "type": "dense_vector",
                        "dims": elastic_config.n_dim,
                        "index": "true",
                        "similarity": elastic_config.distance,
                    },
                },
            }
        }
        return da_schema

    def _build_client(self, elastic_config):

        client = Elasticsearch(
            hosts=self._build_hosts(elastic_config),
            ca_certs=elastic_config.ca_certs,
            basic_auth=elastic_config.basic_auth,
        )

        schema = self._build_schema_from_elastic_config(elastic_config)

        if client.indices.exists(index=elastic_config.index_name):
            client.indices.delete(index=elastic_config.index_name)

        client.indices.create(
            index=elastic_config.index_name, mappings=schema['mappings']
        )
        client.indices.refresh(index=elastic_config.index_name)

        return client

    def _send_requests(self, request):
        bulk(self._client, request)

    def _refresh(self):
        self._client.indices.refresh(index=self._config.index_name)

    def _doc_id_exists(self, doc_id):
        return self._client.exists(index=self._config.index_name, id=doc_id)

    def __getstate__(self):
        pass

    def __setstate__(self, state):
        pass

    def _get_storage_infos(self) -> Dict:
        return {
            'Backend': 'ElasticConfig',
        }

    def _update_offset2ids_meta(self):
        """Update the offset2ids in elastic"""
        if self._client.indices.exists(index=self._index_name_offset2id):
            requests = [
                {
                    '_op_type': 'index',
                    '_id': offset_,  # note offset goes here because it's what we want to get by
                    '_index': self._index_name_offset2id,
                    'blob': f'{id_}',
                }  # id here
                for id_, offset_ in zip(self._offset2ids.ids, self._offset2ids.offsets)
            ]
            r = bulk(self._client, requests)

    def _get_offset2ids_meta(self) -> Tuple[List, List]:
        """Return the offset2ids stored in elastic

        :return: a pair of lists containing offsets and ids

        :raises ValueError: error is raised if index _client is not found or no offsets are found
        """
        if not self._client:
            raise ValueError('Elastic client does not exist')

        n_docs = self._client.count(index=self._index_name_offset2id)["count"]

        if n_docs == 0:
            offsets = [x for x in range(n_docs)]
            resp = self._client.mget(index=self._index_name_offset2id, ids=offsets)
            ids = [x['_source']['blob'] for x in resp['docs']]
            return offsets, ids
        else:
            raise ValueError('No offsets stored in Elastic')
