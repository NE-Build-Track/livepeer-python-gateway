"""Native runner response transport, using loopback only."""

import asyncio
import gzip
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from livepeer_gateway.errors import LivepeerGatewayError, LivepeerHTTPError
from livepeer_gateway.live_runner import call_runner


@asynccontextmanager
async def server(handler):
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("status", [200, 400, 401, 429, 480, 482, 500])
async def test_original_bytes_headers_and_status(status):
    body = b' {"model":"m", "future": true} '
    reply = b' {"future_error": "untouched"} '
    seen = []
    async def handler(request):
        seen.append((await request.read(), request.headers))
        response = web.Response(status=status, body=reply, content_type="application/json")
        response.headers.add("X-Trace", "one")
        response.headers.add("X-Trace", "two")
        return response
    async with server(handler) as url:
        async with await call_runner(url, payload=body, content_type="application/json",
                                     headers={"Anthropic-Version":"2023-06-01", "Accept":"application/json"},
                                     stream=True, return_http_errors=True) as response:
            assert response.status == status
            assert b"".join([part async for part in response.aiter_bytes()]) == reply
            assert response.headers.getall("X-Trace") == ["one", "two"]
    assert len(seen) == 1 and seen[0][0] == body
    assert seen[0][1]["Anthropic-Version"] == "2023-06-01"
    assert seen[0][1].getall("Accept") == ["application/json"]


async def test_default_errors_still_raise_and_402_remains_payment_control():
    async def handler(request):
        return web.Response(status=int(request.path[1:]), body=b"error")
    async with server(handler) as url:
        with pytest.raises(LivepeerHTTPError):
            await call_runner(url+"/400", stream=True)
        with pytest.raises(LivepeerGatewayError, match="signer_url"):
            await call_runner(url+"/402", stream=True, return_http_errors=True)


async def test_native_redirect_is_returned_without_following():
    seen=[]
    async def handler(request):
        seen.append(request.path)
        return web.Response(status=307,headers={"Location":"/other"},body=b"redirect")
    async with server(handler) as url:
        async with await call_runner(url,stream=True,return_http_errors=True) as response:
            assert response.status==307
            assert b"".join([c async for c in response.aiter_bytes()])==b"redirect"
    assert seen==["/"]


async def test_native_encoded_response_bytes_are_not_silently_decompressed():
    raw=gzip.compress(b"original")
    async def handler(request):
        assert request.headers["Accept-Encoding"]=="identity"
        return web.Response(body=raw,headers={"Content-Encoding":"gzip"})
    async with server(handler) as url:
        async with await call_runner(url,stream=True,return_http_errors=True) as response:
            assert b"".join([c async for c in response.aiter_bytes()])==raw


@pytest.mark.parametrize("headers",[
    {"Authorization":"secret"}, {"Livepeer-Payment":"override"},
    {"Content-Type":"text/plain"}, {"Host":"elsewhere"}, {"X-Good":"bad\r\nvalue"},
    {"X-Dup":"1","x-dup":"2"}, {"bad name":"v"},
    {f"x-{i}":"v" for i in range(33)}, {"x-large":"a"*16384},
])
async def test_invalid_headers_rejected_before_any_signer_request(headers):
    with pytest.raises(ValueError):
        await call_runner("https://unused.invalid",signer_url="https://unused.invalid",headers=headers)


async def test_http_errors_option_requires_response_stream():
    with pytest.raises(ValueError,match="stream=True"):
        await call_runner("https://unused.invalid",return_http_errors=True)


async def test_consumer_cancellation_closes_response():
    first=asyncio.Event()
    async def handler(request):
        response=web.StreamResponse()
        await response.prepare(request)
        await response.write(b"first")
        await first.wait()
        return response
    async with server(handler) as url:
        response=await call_runner(url,stream=True,return_http_errors=True)
        try:
            async with response:
                iterator=response.aiter_bytes()
                assert await anext(iterator)==b"first"
                raise asyncio.CancelledError()
        except asyncio.CancelledError:
            pass
        finally:
            first.set()
        assert response._session.closed
