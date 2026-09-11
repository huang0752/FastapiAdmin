from types import SimpleNamespace

import pytest
from test_ai_platform_foundation import MemoryRedis as BaseMemoryRedis

from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.plugin.module_ai.chat.schema import AiFeatureBindingUpdateSchema, AiModelConfigSchema, AiModelConfigUpdateSchema
from app.plugin.module_ai.chat.service import AiFeatureBindingService, AiModelConfigService, resolve_effective_model_config


class MemoryRedis(BaseMemoryRedis):
    def lock(self, name, **kwargs):
        import asyncio
        if not hasattr(self, "locks"):
            self.locks = {}
        return self.locks.setdefault(name, asyncio.Lock())


def auth(user=1, tenant=42):
    return AuthSchema(user=SimpleNamespace(id=user, is_superuser=False, roles=[]), tenant_id=tenant)


def config():
    return AiModelConfigSchema(name='共享模型', base_url='http://127.0.0.1:18119/v1', api_key='test-only-secret-5678', model_id='test-model')


@pytest.mark.asyncio
async def test_shared_model_is_encrypted_and_resolves_for_another_member():
    redis = MemoryRedis()
    manager = AiModelConfigService(auth(), redis, scope='tenant')
    created = await manager.create(config())
    assert 'test-only-secret-5678' not in str(redis.store)
    assert 'api_key_encrypted' not in created and 'api_key' not in created
    assert created['api_key_masked'] == '****5678'
    await manager.update(created['id'], AiModelConfigUpdateSchema(name='改名', base_url=config().base_url, model_id='test-model', api_key=''))
    runtime = await resolve_effective_model_config(redis, auth(user=2))
    assert runtime['api_key'] == config().api_key
    assert runtime['source'] == 'tenant_active'
    other = AiModelConfigService(auth(user=3, tenant=43), redis, scope='tenant')
    assert (await other.list())['items'] == []
    with pytest.raises(CustomException):
        await other.set_active(created['id'])
    with pytest.raises(CustomException):
        await other.delete(created['id'])
    with pytest.raises(CustomException):
        await other.update(created['id'], AiModelConfigUpdateSchema(name='越权', base_url=config().base_url, model_id='x'))
    with pytest.raises(CustomException):
        await AiModelConfigService(auth(tenant=None), redis, scope='tenant').list()


@pytest.mark.asyncio
async def test_personal_override_and_bound_model_delete_guard():
    redis = MemoryRedis()
    shared = AiModelConfigService(auth(), redis, scope='tenant')
    model = await shared.create(config())
    personal = await AiModelConfigService(auth(), redis).create(config())
    assert (await resolve_effective_model_config(redis, auth()))['config_id'] == personal['id']
    binding = AiFeatureBindingService(auth(), redis, scope='tenant')
    await binding.upsert('demo_data.blueprint', AiFeatureBindingUpdateSchema(model_config_id=model['id'], enabled=True))
    assert (await AiFeatureBindingService(auth(user=2), redis, scope='tenant').get('demo_data.blueprint'))['enabled']
    with pytest.raises(CustomException, match='绑定'):
        await shared.delete(model['id'])
    await binding.upsert('demo_data.blueprint', AiFeatureBindingUpdateSchema(enabled=False))
    await shared.delete(model['id'])
    assert (await shared.list())['active_id'] is None


@pytest.mark.asyncio
async def test_management_requires_current_system_governance_role(monkeypatch):
    from app.plugin.module_ai.chat import tenant_config
    async def permissions(actor):
        return set()
    monkeypatch.setattr(tenant_config, 'resolve_effective_permissions', permissions)
    actor = auth()
    assert not await tenant_config.can_manage(actor)
    role = SimpleNamespace(code='owner', tenant_id=42, status=0, is_system=False)
    actor.user.roles = [role]
    assert not await tenant_config.can_manage(actor)
    role.is_system = True
    assert await tenant_config.can_manage(actor)
    role.tenant_id = 43
    assert not await tenant_config.can_manage(actor)
    role.tenant_id = 42
    role.status = 1
    assert not await tenant_config.can_manage(actor)
    actor.user.is_superuser = True
    assert await tenant_config.can_manage(actor)
    actor.tenant_id = None
    assert not await tenant_config.can_manage(actor)


@pytest.mark.asyncio
async def test_tenant_runtime_shares_binding_and_validates_output(monkeypatch):
    from pydantic import BaseModel

    from app.plugin.module_ai.chat import service as module
    class Result(BaseModel):
        message: str
    async def audit(*args, **kwargs):
        pass
    monkeypatch.setattr(module, 'record_ai_call_audit', audit)
    redis = MemoryRedis()
    model = await AiModelConfigService(auth(), redis, scope='tenant').create(config())
    await AiFeatureBindingService(auth(), redis, scope='tenant').upsert('demo_data.blueprint', AiFeatureBindingUpdateSchema(model_config_id=model['id'], enabled=True))
    async def runner(**kwargs):
        assert kwargs['model_config']['api_key'] == config().api_key
        return {'message': 'shared result'}
    result = await module.AiRuntimeService(auth(user=2), redis, scope='tenant', runner=runner).structured_generate(feature_code='demo_data.blueprint', prompt='hello', response_model=Result)
    assert result.message == 'shared result'
    with pytest.raises(CustomException):
        await module.AiRuntimeService(auth(user=2, tenant=43), redis, scope='tenant', runner=runner).structured_generate(feature_code='demo_data.blueprint', prompt='hello', response_model=Result)


@pytest.mark.asyncio
async def test_concurrent_shared_model_writes_preserve_both_models():
    import asyncio
    class YieldingRedis(MemoryRedis):
        async def get(self, name):
            result = await super().get(name)
            await asyncio.sleep(0)
            return result
    redis = YieldingRedis()
    service = AiModelConfigService(auth(), redis, scope="tenant")
    await asyncio.gather(service.create(config()), service.create(config()))
    assert len((await service.list())["items"]) == 2


@pytest.mark.asyncio
async def test_shared_configuration_has_no_expiry_and_write_failure_is_not_success():
    class ExpiryRedis(MemoryRedis):
        async def set(self, name, value, ex=None, nx=False):
            assert ex is None, 'AI configuration must not expire after 24 hours'
            return await super().set(name, value, ex=ex, nx=nx)
    redis = ExpiryRedis()
    created = await AiModelConfigService(auth(), redis, scope='tenant').create(config())
    assert len((await AiModelConfigService(auth(), redis, scope='tenant').list())['items']) == 1
    assert created['id']


@pytest.mark.asyncio
async def test_http_management_denials_and_cross_tenant_ids():
    import httpx
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    from app.core.dependencies import get_current_user, redis_getter
    from app.plugin.module_ai.chat.tenant_config import router
    app = FastAPI()
    app.include_router(router)
    actor = auth()
    actor.user.is_superuser = True
    redis = MemoryRedis()
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[redis_getter] = lambda: redis
    @app.exception_handler(CustomException)
    async def handler(request: Request, exc: CustomException):
        return JSONResponse(status_code=exc.status_code, content={'code':exc.code})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        created = await client.post('/ai-config/model', json=config().model_dump())
        assert created.status_code == 200
        model_id = created.json()['data']['id']
        actor.tenant_id = 43
        assert (await client.get('/ai-config/model')).json()['data']['items'] == []
        assert (await client.post(f'/ai-config/model/{model_id}/probe')).status_code == 404
        actor.user.is_superuser = False
        for method, path, data in [
            ('GET','/ai-config/model',None), ('POST','/ai-config/model',config().model_dump()),
            ('PUT',f'/ai-config/model/{model_id}',config().model_dump()), ('DELETE',f'/ai-config/model/{model_id}',None),
            ('POST',f'/ai-config/model/{model_id}/activate',None), ('POST',f'/ai-config/model/{model_id}/probe',None),
            ('GET','/ai-config/feature',None), ('PUT','/ai-config/feature/demo_data.blueprint',{'enabled':True}),
        ]:
            assert (await client.request(method,path,json=data)).status_code == 403


@pytest.mark.asyncio
async def test_store_rejection_does_not_report_saved_model():
    class RejectRedis(MemoryRedis):
        async def set(self, **kwargs):
            return False
    with pytest.raises(CustomException, match='保存失败'):
        await AiModelConfigService(auth(), RejectRedis(), scope='tenant').create(config())


def test_runtime_can_be_imported_directly_by_product_workers():
    import subprocess
    import sys
    from pathlib import Path
    result = subprocess.run([sys.executable, '-c', 'from app.plugin.module_ai.chat.service import AiRuntimeService'], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
