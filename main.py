import asyncio
import logging
import weakref

import uvloop
import aiohttp
from aiohttp import web, WSCloseCode
import aioredis


asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


async def chanel_list(request):
    return web.json_response(data={'list': list(request.app['websockets'].keys())})


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    channel = request.match_info.get('channel')
    if channel in request.app['websockets']:
        request.app['websockets'].get(channel).add(ws)
    else:
        request.app['websockets'][channel] = weakref.WeakSet(data=(ws,))

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await request.app['redis'].publish(f'channel:{channel}', msg.data)
    finally:
        request.app['websockets'][channel].discard(ws)
        if not request.app['websockets'].get(channel):
            del request.app['websockets'][channel]

    return ws


async def listen_to_redis(app):
    try:
        ch, *_ = await app['redis'].psubscribe('channel:*')
        while await ch.wait_message():
            channel, msg = await ch.get(encoding='utf-8')
            *_, channel = channel.decode('utf-8').split(':')
            if app['websockets'].get(channel):
                for ws in app['websockets'].get(channel):
                    await ws.send_str(msg)
    except asyncio.CancelledError:
        pass
    finally:
        if not app['redis'].closed:
            await app['redis'].punsubscribe(ch.name)


async def start_background_tasks(app):
    loop = asyncio.get_running_loop()
    app['redis_listener'] = loop.create_task(listen_to_redis(app))


async def on_cleanup(app):
    app['redis_listener'].cancel()
    app['redis'].close()
    await app['redis'].wait_closed()


async def on_shutdown(app):
    for ws in set(ws for ref in app['websockets'].values() for ws in ref):
        await ws.close(code=WSCloseCode.GOING_AWAY,
                       message='Server shutdown')


async def create_app(*args, **kwargs):
    app = web.Application()
    redis = await aioredis.create_redis_pool('redis://localhost')

    app.update(
        redis=redis,
        websockets={},
    )

    app.add_routes([
        web.get('/list', chanel_list),
        web.get('/ws/{channel}', websocket_handler),
    ])

    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(on_cleanup)
    app.on_shutdown.append(on_shutdown)

    return app


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='aiohttp server')
    parser.add_argument('--host')
    parser.add_argument('--port')
    parser.add_argument('--path')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    fh = logging.FileHandler('access.log')
    fh.setLevel(logging.DEBUG)
    _logger = logging.getLogger('aiohttp.access')
    _logger.addHandler(fh)
    _logger.setLevel(logging.INFO)

    web.run_app(create_app(), host=args.host, port=args.port, path=args.path)
