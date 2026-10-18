from muse_orchestrator.core.security import SecretVault
from muse_orchestrator.services.generation_queue import compute_backoff_seconds


def test_backoff_has_real_jitter_and_stays_bounded():
    values = {compute_backoff_seconds(3) for _ in range(30)}
    assert len(values) > 1  # jitter is random, not a per-process constant
    assert all(0 < v <= 60.0 for v in values)


def test_secret_mask_reveals_little():
    assert SecretVault.mask("sk-abcdefghijklmnop") == "sk...op"
    assert SecretVault.mask("short") == "********"
    assert SecretVault.mask(None) == "[NOT_SET]"


def test_dispatch_lease_model_is_registered_with_metadata():
    from muse_orchestrator.models.domain import Base, GenerationDispatchLeaseRecord

    assert GenerationDispatchLeaseRecord.__tablename__ == "generation_dispatch_leases"
    assert "generation_dispatch_leases" in Base.metadata.tables
