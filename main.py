import asyncio
import logging
import weakref
from collections import defaultdict

import uvloop
from aiohttp import web, WSCloseCode


asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


async def chanel_list(request):
    return web.json_response(data=list(request.app['websockets'].keys()))


async def websocket_handler(request):
    ws = web.WebSocketResponse()
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


async def on_shutdown(app):
    for ws in set(ws for ref in app['websockets'].values() for ws in ref):
        await ws.close(code=WSCloseCode.GOING_AWAY,
                       message='Server shutdown')


async def create_app(*args, **kwargs):
    app = web.Application()

    app.update(websockets=defaultdict(weakref.WeakSet))

    app.add_routes([
        web.get('/list', chanel_list),
        web.get('/ws/{channel}', websocket_handler),
    ])

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

    fh = logging.FileHandler('server.log')
    fh.setLevel(logging.DEBUG)
    _logger = logging.getLogger('aiohttp.server')
    _logger.addHandler(fh)
    _logger.setLevel(logging.INFO)

    web.run_app(create_app(), host=args.host, port=args.port, path=args.path)
