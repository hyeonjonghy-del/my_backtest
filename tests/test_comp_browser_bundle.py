import base64
import hashlib
import pytest
from strategies.comp.browser_bundle import decode_bundle


def payload(content=b'example'):
    return dict(action='bundle', base64=base64.b64encode(content).decode(), digest=hashlib.sha256(content).hexdigest())


def test_browser_bytes_are_exact_and_malformed_or_tampered_messages_fail():
    assert decode_bundle(payload()) == b'example'
    wrong = payload(); wrong['base64'] = base64.b64encode(b'changed').decode()
    with pytest.raises(ValueError): decode_bundle(wrong)
    for value in (None, {'action': 'clear'}, {'action': 'bundle', 'base64': 'bad', 'digest': '0' * 64}, payload(b'')):
        with pytest.raises(ValueError): decode_bundle(value)


def test_oversized_encoded_data_rejected_before_decoding(monkeypatch):
    import strategies.comp.browser_bundle as module
    monkeypatch.setattr(module, 'MAX_BYTES', 2)
    with pytest.raises(ValueError): decode_bundle(payload(b'large'))


def test_chunked_transfer_only_returns_complete_verified_bytes(monkeypatch):
    import strategies.comp.browser_bundle as module
    monkeypatch.setattr(module, 'CHUNK_BYTES', 2)
    monkeypatch.setattr(module, 'MAX_BYTES', 6)
    digest = hashlib.sha256(b'abcdef').hexdigest()
    received = None
    for i, piece in enumerate([b'ab', b'cd', b'ef']):
        value = dict(action='chunk', part=i, total=3, transfer_id='test', digest=digest,
                     base64=base64.b64encode(piece).decode())
        content, received, changed = module.receive_chunk(value, received)
        assert changed
        assert content == (b'abcdef' if i == 2 else None)
        again, received, changed = module.receive_chunk(value, received)
        assert not changed and again == content
    value['base64'] = base64.b64encode(b'XX').decode()
    with pytest.raises(ValueError): module.receive_chunk(value, received)
    value['part'] = 1; value['transfer_id'] = 'new'
    with pytest.raises(ValueError): module.receive_chunk(value, received)


def test_chunk_full_digest_and_size_are_checked(monkeypatch):
    import strategies.comp.browser_bundle as module
    monkeypatch.setattr(module, 'CHUNK_BYTES', 2)
    monkeypatch.setattr(module, 'MAX_BYTES', 2)
    value = dict(action='chunk',part=0,total=1,transfer_id='id',digest='0'*64,base64=base64.b64encode(b'ab').decode())
    with pytest.raises(ValueError): module.receive_chunk(value)
    value['base64'] = base64.b64encode(b'abc').decode()
    with pytest.raises(ValueError): module.receive_chunk(value)
