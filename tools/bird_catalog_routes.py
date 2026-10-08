"""鸟名目录 HTTP 路由，不改变识鸟协议。 / Additive catalog HTTP routes."""
import sqlite3

from flask import Blueprint, current_app, jsonify, request
from tools.bird_catalog import BirdCatalog, CatalogNotFound, CatalogAmbiguous

catalog_api = Blueprint('bird_catalog', __name__)


def _integer(key, default=None):
    """解析可选整数，拒绝无效参数。 / Validate optional integer parameters."""
    raw = request.args.get(key)
    if raw is None:
        return default
    value = int(raw)
    if not (0 if key == 'offset' else 1) <= value <= 9223372036854775807:
        raise ValueError(f'{key} 超出有效范围 / Integer out of range')
    return value


def _respond(detail=False):
    """统一输入与故障响应；不把数据库故障伪装成空结果。 / Explicit error responses."""
    try:
        catalog = BirdCatalog()
        version = _integer('version_id')
        if detail:
            result = catalog.detail(bird_id=_integer('bird_id'), name=request.args.get('name'), version_id=version)
        else:
            result = catalog.search(request.args.get('q', ''), version_id=version,
                                    limit=_integer('limit', 100), offset=_integer('offset', 0))
        return jsonify(result)
    except CatalogNotFound as exc:
        return jsonify(success=False, error=str(exc)), 404
    except CatalogAmbiguous as exc:
        return jsonify(success=False, error=str(exc)), 409
    except (ValueError, TypeError) as exc:
        return jsonify(success=False, error=str(exc)), 400
    except (OSError, sqlite3.Error):
        current_app.logger.exception('[BirdCatalog] database unavailable')
        return jsonify(success=False, error='鸟名或参考数据库不可用 / Catalog database unavailable'), 503


@catalog_api.route('/birds/search', methods=['GET'])
def search_birds():
    """查询/列出鸟名。 / Search or list bird names."""
    return _respond()


@catalog_api.route('/birds/detail', methods=['GET'])
def bird_detail():
    """指定鸟种详情。 / Retrieve the selected species."""
    return _respond(detail=True)
