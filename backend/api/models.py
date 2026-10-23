import asyncio

from fastapi import APIRouter, Depends, Request

from backend.api.deps import admin, viewer

router = APIRouter(prefix="/api/v1/models", tags=["Models"])


@router.get("")
async def models(request: Request, user=Depends(viewer)):
    return await request.app.state.models.list()


@router.post("/pull", status_code=202)
async def pull(data: dict, request: Request, user=Depends(admin)):
    result = await request.app.state.models.pull(str(data.get("name", "")))
    request.app.state.store.audit(user["username"], "model.pull", result["id"])
    return result


@router.post("/cancel")
async def cancel(request: Request, user=Depends(admin)):
    task = request.app.state.models.job_task
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return {"success": True}


@router.post("/verify")
async def verify(data: dict, request: Request, user=Depends(admin)):
    return await request.app.state.models.verify(str(data.get("name", "")))


@router.post("/warm")
async def warm(data: dict, request: Request, user=Depends(admin)):
    return await request.app.state.models.verify(str(data.get("name", "")), warm=True)


@router.post("/unload")
async def unload(data: dict, request: Request, user=Depends(admin)):
    await request.app.state.models.unload(str(data.get("name", "")))
    return {"success": True}


@router.post("/default")
async def default(data: dict, request: Request, user=Depends(admin)):
    name = str(data.get("name", ""))
    await request.app.state.models.digest(name)
    request.app.state.store.set_setting("default_model", name)
    return {"name": name}


@router.post("/benchmark", status_code=202)
async def benchmark(data: dict, request: Request, user=Depends(admin)):
    return await request.app.state.evaluations.start(data)


@router.delete("/{name:path}")
async def delete(name: str, request: Request, user=Depends(admin)):
    await request.app.state.models.delete(name)
    request.app.state.store.audit(user["username"], "model.delete", name)
    return {"success": True}
