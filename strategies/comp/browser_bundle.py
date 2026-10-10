"""A browser-owned ZIP cache. No server disk or shared user storage."""
import base64
import binascii
import hashlib
from pathlib import Path
import streamlit.components.v1 as components

MAX_BYTES = 100 * 1024**2
CHUNK_BYTES = 6 * 1024**2
_component = components.declare_component('comp_browser_bundle_v2', path=str(Path(__file__).with_name('browser_bundle')))


def browser_bundle(accepted_digest=None, rejected_digest=None, received=None):
    return _component(accepted_digest=accepted_digest, rejected_digest=rejected_digest,
                      received_id=received.get('id') if received else None,
                      received_part=len(received['parts']) - 1 if received else -1,
                      max_bytes=MAX_BYTES, chunk_bytes=CHUNK_BYTES, default=None, key='comp_browser_upload')


def receive_chunk(value, received=None):
    """Accept sequential, bounded pieces; expose ZIP only after full SHA validation."""
    if not isinstance(value, dict) or value.get('action') != 'chunk':
        raise ValueError('브라우저 전송 자료 형식이 올바르지 않습니다.')
    part, total, identifier, digest = (value.get(k) for k in ('part', 'total', 'transfer_id', 'digest'))
    if (type(part) is not int or type(total) is not int or not 0 <= part < total
            or not 1 <= total <= (MAX_BYTES + CHUNK_BYTES - 1) // CHUNK_BYTES
            or not isinstance(identifier, str) or not 1 <= len(identifier) <= 80
            or not isinstance(digest, str) or len(digest) != 64):
        raise ValueError('브라우저 ZIP 전송 순서가 올바르지 않습니다.')
    encoded = value.get('base64')
    if not isinstance(encoded, str) or len(encoded) > 4 * ((CHUNK_BYTES + 2) // 3):
        raise ValueError('브라우저 ZIP 전송 조각이 너무 큽니다.')
    try:
        piece = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError('브라우저 ZIP 전송 자료를 읽을 수 없습니다.') from error
    if not piece or len(piece) > CHUNK_BYTES or (part < total - 1 and len(piece) != CHUNK_BYTES):
        raise ValueError('브라우저 ZIP 전송 크기가 올바르지 않습니다.')
    if received is None or received['id'] != identifier:
        if part != 0:
            raise ValueError('브라우저 ZIP을 처음부터 다시 선택하세요.')
        received = dict(id=identifier, digest=digest, total=total, parts=[])
    if received['digest'] != digest or received['total'] != total or part > len(received['parts']):
        raise ValueError('브라우저 ZIP 전송 순서가 올바르지 않습니다.')
    changed = part == len(received['parts'])
    if changed:
        received['parts'].append(piece)
    elif received['parts'][part] != piece:
        raise ValueError('브라우저 ZIP 전송 자료가 변경되었습니다.')
    if len(received['parts']) == total and 'content' not in received:
        content = b''.join(received['parts'])
        if len(content) > MAX_BYTES or hashlib.sha256(content).hexdigest() != digest:
            raise ValueError('브라우저 ZIP 전송 확인에 실패했습니다.')
        received['content'] = content
    return received.get('content'), received, changed


def decode_bundle(value):
    if not isinstance(value, dict) or value.get('action') != 'bundle':
        raise ValueError('브라우저 ZIP 자료 형식이 올바르지 않습니다.')
    encoded = value.get('base64')
    digest = value.get('digest')
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_BYTES + 2) // 3):
        raise ValueError('ZIP은 100MB 이하만 사용할 수 있습니다.')
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError('브라우저 ZIP 확인 정보가 올바르지 않습니다.')
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError('브라우저 ZIP 자료를 읽을 수 없습니다.') from error
    if not content or len(content) > MAX_BYTES or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError('브라우저 ZIP 자료의 크기 또는 확인 정보가 올바르지 않습니다.')
    return content
