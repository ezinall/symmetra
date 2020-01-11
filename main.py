import asyncio
import logging
import weakref

import uvloop
import aiohttp
from aiohttp import web, WSCloseCode


asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


async def chanel_list(request):
    return web.json_response(data={'list': list(request.app['websockets'].keys())})


async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    chanel = request.match_info.get('chanel')
    if chanel in request.app['websockets']:
        request.app['websockets'][chanel].add(ws)
    else:
        request.app['websockets'][chanel] = weakref.WeakSet(data=(ws,))

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                for i in request.app['websockets'][chanel]:
                    if i is not ws:
                        await i.send_str(msg.data)
    finally:
        request.app['websockets'][chanel].discard(ws)
        if not request.app['websockets'][chanel]:
            del request.app['websockets'][chanel]

    return ws


async def on_shutdown(app):
    for ws in set(ws for ref in app['websockets'].values() for ws in ref):
        await ws.close(code=WSCloseCode.GOING_AWAY,
                       message='Server shutdown')


async def create_app(*args, **kwargs):
    app = web.Application()

    app.update(websockets={})

    app.add_routes([
        web.get('/list', chanel_list),
        web.get('/ws/{chanel}', websocket_handler),
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

    web.run_app(create_app(), host=args.host, port=args.port, path=args.path)
