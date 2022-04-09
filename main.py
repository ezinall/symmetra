import asyncio
import logging
import weakref
from collections import defaultdict

import async_timeout
import redis.asyncio as redis
import uvloop
from aiohttp import web, WSCloseCode

uvloop.install()


async def channel_list(request):
    return web.json_response(data=list(request.app['websockets'].keys()))


async def websocket_handler(request):
    ws = web.WebSocketResponse(heartbeat=55)
    await ws.prepare(request)

    channel = request.match_info.get('channel')
    request.app['websockets'][channel].add(ws)

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                for i in set(request.app['websockets'][channel]):
                    if i is not ws:
                        await i.send_str(msg.data)
    finally:
        request.app['websockets'].get(channel).discard(ws)
        if not request.app['websockets'].get(channel):
            del request.app['websockets'][channel]

    return ws


async def listen_to_redis(app):
    pubsub = app['pubsub']
    await pubsub.psubscribe('ws.*')

    while True:
        try:
            async with async_timeout.timeout(1):
                message = await pubsub.get_message(ignore_subscribe_messages=True)
                if message is not None:
                    *_, socket_channel = message['channel'].decode().split('.', maxsplit=1)
                    for i in set(app['websockets'][socket_channel]):
                        await i.send_str(message['data'].decode())
                await asyncio.sleep(0.01)
        except asyncio.TimeoutError:
            pass
        except RuntimeError:
            break


async def start_background_tasks(app):
    app['redis_listener'] = asyncio.create_task(listen_to_redis(app))


async def cleanup_background_tasks(app):
    await app['pubsub'].close()
    await app['redis'].close()


async def on_shutdown(app):
    for ws in set(ws for ref in app['websockets'].values() for ws in ref):
        await ws.close(code=WSCloseCode.GOING_AWAY,
                       message='Server shutdown')


async def create_app(redis_host, *args, **kwargs):
    app = web.Application()

    redis_connection = redis.from_url(redis_host)
    pubsub: redis.client.PubSub = redis_connection.pubsub()

    app.update(
        redis=redis_connection,
        pubsub=pubsub,
        websockets=defaultdict(weakref.WeakSet)
    )

    app.add_routes([
        web.get('/ws/list', channel_list),
        web.get('/ws/channel/{channel}', websocket_handler),
    ])

    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    app.on_shutdown.append(on_shutdown)

    return app


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='aiohttp server')
    parser.add_argument('--hostname')
    parser.add_argument('--port')
    parser.add_argument('--path')

    parser.add_argument('--redis_host', default='redis://localhost')

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    fh = logging.FileHandler('access.log')
    fh.setLevel(logging.DEBUG)
    _logger = logging.getLogger('aiohttp.access')
    _logger.addHandler(fh)
    _logger.setLevel(logging.INFO)

    fh = logging.FileHandler('server.log')
    fh.setLevel(logging.DEBUG)
    _logger = logging.getLogger('aiohttp.server')
    _logger.addHandler(fh)
    _logger.setLevel(logging.INFO)

    web.run_app(create_app(redis_host=args.redis_host), host=args.hostname, port=args.port, path=args.path)
