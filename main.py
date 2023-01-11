import asyncio
import logging
import os
import sys
import weakref
from collections import defaultdict

from aiohttp import web, WSCloseCode, WSMessage
import redis.exceptions as redis_exceptions
import redis.asyncio as redis
import uvloop

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

REDIS_HOST = os.getenv('REDIS_HOST', 'redis://localhost')

fh = logging.FileHandler('access.log')
fh.setLevel(logging.DEBUG)
_logger = logging.getLogger('aiohttp.access')
_logger.addHandler(fh)
_logger.setLevel(logging.ERROR)

fh = logging.FileHandler('server.log')
fh.setLevel(logging.DEBUG)
_logger = logging.getLogger('aiohttp.server')
_logger.addHandler(fh)
_logger.setLevel(logging.ERROR)


async def channel_list(request: web.Request) -> web.Response:
    return web.json_response(data=list(request.app['websockets'].keys()))


async def websocket_handler(request: web.Request) -> web.StreamResponse:
    ws = web.WebSocketResponse(heartbeat=55)
    await ws.prepare(request)

    channel = request.match_info.get('channel')
    request.app['websockets'][channel].add(ws)

    try:
        async for msg in ws:
            msg: WSMessage
            if msg.type == web.WSMsgType.TEXT:
                for i in set(request.app['websockets'][channel]):
                    if i is not ws:
                        await i.send_str(msg.data)
    finally:
        request.app['websockets'].get(channel).discard(ws)
        if not request.app['websockets'].get(channel):
            del request.app['websockets'][channel]

    return ws


async def liveliness(_) -> web.Response:
    return web.HTTPOk()


async def readiness(request) -> web.Response:
    try:
        await request.app['redis'].ping()
        return web.HTTPOk()
    except redis_exceptions.ConnectionError:
        return web.HTTPInternalServerError()


async def listen_to_redis(app: web.Application) -> None:
    pubsub: redis.client.PubSub = app['pubsub']
    await pubsub.psubscribe('ws.*')

    while True:
        try:
            message: dict = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1)

            if message is not None:
                *_, ws_channel = message['channel'].decode().\
                    split('.', maxsplit=1)
                for i in app['websockets'].get(ws_channel, ()):
                    await i.send_str(message['data'].decode())

        except redis_exceptions.ConnectionError as e:  # если отвалился сервер
            logging.error('', exc_info=e)
            sys.exit(1)


async def start_background_tasks(app: web.Application) -> None:
    app['redis_listener'] = asyncio.create_task(listen_to_redis(app))


async def cleanup_background_tasks(app: web.Application) -> None:
    app['redis_listener'].cancel()
    # await app['redis_listener']

    await app['pubsub'].close()
    await app['redis'].close()


async def on_shutdown(app):
    for ws in [ws for ref in app['websockets'].values() for ws in ref]:
        await ws.close(code=WSCloseCode.GOING_AWAY,
                       message='Server shutdown')


async def create_app(*args, **kwargs):
    app: web.Application = web.Application()

    r: redis.Redis = await redis.from_url(REDIS_HOST)
    await r.ping()
    pubsub: redis.client.PubSub = r.pubsub()

    app.update(
        redis=r,
        pubsub=pubsub,
        websockets=defaultdict(weakref.WeakSet),
    )

    app.add_routes([
        web.get('/ws/channel/{channel}', websocket_handler),
        web.get('/ws/list', channel_list),
        web.get('/ws/healthz', liveliness),
        web.get('/ws/readiness', readiness),
    ])

    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    app.on_shutdown.append(on_shutdown)

    return app
