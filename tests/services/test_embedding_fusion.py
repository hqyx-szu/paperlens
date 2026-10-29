from __future__ import annotations

import numpy as np

from app.services.embeddings import pack_vector, unpack_vector
from app.services.search import EvidenceHit, _fuse_hits


def _hit(chunk_id: int, paper_id: int = 1, score: float = 0.0) -> EvidenceHit:
    return EvidenceHit(
        paper_id=paper_id,
        chunk_id=chunk_id,
        page=1,
        quote="q",
        content="content",
        score=score,
    )


def test_pack_and_unpack_roundtrip() -> None:
    values = [0.1, -0.2, 0.3, 0.4]

    packed = pack_vector(values)
    restored = unpack_vector(packed, len(values))

    assert packed == np.asarray(values, dtype=np.float32).tobytes()
    assert restored.dtype == np.float32
    assert np.allclose(restored, np.asarray(values, dtype=np.float32))


def test_fuse_hits_without_vector_keeps_keyword_order() -> None:
    keyword = [_hit(3), _hit(1), _hit(2)]

    assert _fuse_hits(keyword, []) == keyword


def test_fuse_hits_promotes_chunk_found_by_both() -> None:
    keyword = [_hit(10), _hit(11), _hit(12)]
    vector = [_hit(12), _hit(13)]

    fused = _fuse_hits(keyword, vector)

    # chunk 12 在两路都靠前，RRF 分数最高；13 只在向量路出现，仍进入候选。
    assert [hit.chunk_id for hit in fused] == [12, 10, 11, 13]


def test_fuse_hits_vector_only_when_keyword_empty() -> None:
    vector = [_hit(5), _hit(6)]

    assert _fuse_hits([], vector) == vector
