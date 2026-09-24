import asyncio
import json
import os
import sqlite3
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr
from accounts import password_hash

router = APIRouter()
COOKIE = 'hongguo_session'
SECURE = os.environ.get('HONGGUO_ENV') == 'production'


def store(request):
    return request.app.state.accounts


def require_user(request):
    user = getattr(request.state, 'user', None)
    if not user:
        raise HTTPException(401, '请先登录')
    return user


def require_admin(request):
    user = require_user(request)
    if user['role'] != 'admin':
        raise HTTPException(403, '需要管理员权限')
    return user


def login_response(request, user):
    token = store(request).login(user['id'])
    response = JSONResponse({'user': user})
    response.set_cookie(COOKIE, token, max_age=7*86400, httponly=True, secure=SECURE, samesite='lax', path='/')
    return response


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r'^[A-Za-z0-9_]+$')
    password: SecretStr = Field(min_length=10, max_length=128)


class Setup(Credentials):
    token: str = Field(min_length=30, max_length=100)


class UserUpdate(BaseModel):
    disabled: bool | None = None
    password: SecretStr | None = Field(default=None, min_length=10, max_length=128)


class ShareInput(BaseModel):
    book_id: str = Field(pattern=r'^\d{1,24}$')
    title: str = Field(min_length=1, max_length=200)
    cover: str = Field(default='', max_length=2048)
    abstract: str = Field(default='', max_length=5000)
    kind: Literal['drama', 'manju', 'ai'] = 'drama'
    days: int = Field(default=0, ge=0, le=3650)
    hours: int | None = Field(default=None, ge=1, le=24*30)


class Report(BaseModel):
    region: Literal['unverified', 'local-direct', 'local-proxy', 'mainland', 'overseas'] = 'unverified'
    title: str = Field(max_length=200)
    item_id: str = Field(pattern=r'^\d{1,24}$')
    definition: str = Field(max_length=20)
    startup_ms: float = Field(ge=0, le=600000)
    preparation_ms: float = Field(ge=0, le=600000)
    watched_seconds: float = Field(ge=0, le=86400)
    stall_count: int = Field(ge=0, le=10000)
    stall_seconds: float = Field(ge=0, le=86400)
    dropped_frames: int = Field(ge=0, le=10000000)
    total_frames: int = Field(ge=0, le=10000000)
    cached: bool


@router.get('/api/auth/status')
async def auth_status(request: Request):
    return {'setup_required': store(request).needs_setup()}


@router.post('/api/auth/setup')
async def setup(request: Request, body: Setup):
    if not store(request).limit('setup:'+request.client.host, maximum=10):
        raise HTTPException(429, '尝试过于频繁，请 5 分钟后再试')
    encoded = await asyncio.to_thread(password_hash, body.password.get_secret_value())
    try:
        store(request).setup(body.token, body.username, encoded)
    except ValueError as exc:
        raise HTTPException(403, str(exc))
    user = await asyncio.to_thread(store(request).authenticate, body.username, body.password.get_secret_value())
    return login_response(request, user)


@router.post('/api/auth/login')
async def login(request: Request, body: Credentials):
    accounts = store(request)
    if not accounts.limit('ip:'+request.client.host, maximum=30) or not accounts.limit('name:'+body.username.lower(), maximum=10):
        raise HTTPException(429, '登录尝试过多，请 5 分钟后再试')
    user = await asyncio.to_thread(accounts.authenticate, body.username, body.password.get_secret_value())
    if not user:
        raise HTTPException(401, '账号或密码不正确，或账号已停用')
    accounts.logout(request.cookies.get(COOKIE))
    return login_response(request, user)


@router.post('/api/auth/logout')
async def logout(request: Request):
    store(request).logout(request.cookies.get(COOKIE))
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE, path='/', secure=SECURE, httponly=True, samesite='lax')
    return response


@router.get('/api/auth/me')
async def me(request: Request):
    return {'user': require_user(request)}


@router.get('/api/admin/users')
async def users(request: Request):
    require_admin(request)
    return {'items': store(request).users()}


@router.post('/api/admin/users')
async def create_user(request: Request, body: Credentials):
    require_admin(request)
    encoded = await asyncio.to_thread(password_hash, body.password.get_secret_value())
    try:
        uid = store(request).create_user(body.username, encoded)
    except sqlite3.IntegrityError:
        raise HTTPException(409, '账号名称已存在')
    return {'id': uid}


@router.patch('/api/admin/users/{user_id}')
async def update_user(user_id: int, request: Request, body: UserUpdate):
    require_admin(request)
    encoded = await asyncio.to_thread(password_hash, body.password.get_secret_value()) if body.password else None
    try:
        store(request).update_user(user_id, body.disabled, encoded)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {'ok': True}


@router.post('/api/admin/shares')
async def create_share(request: Request, body: ShareInput):
    from endpoints.duanju import duanju_catalog
    admin = require_admin(request)
    if not store(request).limit('shares:'+str(admin['id']), maximum=30):
        raise HTTPException(429, '创建频率过高，请稍后再试')
    response = await duanju_catalog(request, body.book_id)
    payload = json.loads(response.body)
    if response.status_code != 200 or payload.get('code') != 0:
        raise HTTPException(502, payload.get('msg') or '无法取得剧集目录')
    episodes = (payload.get('data') or {}).get('items') or []
    episodes = [{k: ep.get(k) for k in ('item_id', 'index', 'title')} for ep in episodes if str(ep.get('item_id', '')).isdigit()]
    if not episodes:
        raise HTTPException(502, '剧集目录为空，暂时无法创建分享')
    item = body.model_dump(exclude={'hours', 'days'}) | {'episode_count': len(episodes)}
    share = store(request).create_share(admin['id'], item, episodes, False, body.days*24 if 'days' in body.model_fields_set else (body.hours or 0))
    return {'id': share['id'], 'path': '/s/'+share['token'], 'expires': share['expires']}


@router.get('/api/admin/shares')
async def shares(request: Request):
    require_admin(request)
    return {'items': store(request).shares()}


@router.delete('/api/admin/shares/{share_id}')
async def revoke(share_id: str, request: Request):
    require_admin(request)
    if not store(request).revoke(share_id):
        raise HTTPException(404, '链接不存在')
    return {'ok': True}


@router.post('/api/reports')
async def report(request: Request, body: Report):
    user = require_user(request)
    if not store(request).limit('reports:'+str(user['id']), maximum=60):
        raise HTTPException(429, '测试记录过于频繁')
    store(request).report(user['id'], body.model_dump())
    return {'ok': True}


@router.get('/api/admin/reports')
async def reports(request: Request):
    require_admin(request)
    return {'items': store(request).reports()}
