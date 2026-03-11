"""Assemble all HTTP route sub-routers into one top-level router."""

from fastapi import APIRouter

from . import assets, crud, device, models, settings

router = APIRouter()

router.include_router(settings.router)
router.include_router(device.router)
router.include_router(models.router)
router.include_router(assets.router)
router.include_router(crud.router)
