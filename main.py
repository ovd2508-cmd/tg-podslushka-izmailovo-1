import asyncio
import os
import logging

import uvicorn

from bot import bot, dp, setup_menu
from config import WEB_HOST, WEB_PORT, DB_PATH
from fastapi_app import app


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("main")


async def main():
    web = None
    polling = None
    server = None

    try:
        me = await bot.get_me()
        await bot.delete_webhook(drop_pending_updates=False)
        await setup_menu()

        logger.info("Бот @%s. База: %s", me.username, DB_PATH)

        server = uvicorn.Server(
            uvicorn.Config(
                app=app,
                host=WEB_HOST,
                port=WEB_PORT,
                use_colors=False,
                timeout_graceful_shutdown=10,
            )
        )

        web = asyncio.create_task(server.serve())

        while not server.started:
            if web.done():
                await web
                return

            await asyncio.sleep(0.1)

        if os.getenv("RUN_BOT", "1") == "0":
            logger.warning(
                "Режим переноса базы: веб-сервер работает, polling отключён"
            )
            await web
            return

        polling = asyncio.create_task(
            dp.start_polling(
                bot,
                handle_signals=False,
                close_bot_session=False,
                handle_as_tasks=False,
                allowed_updates=dp.resolve_used_update_types(),
            )
        )

        done, _ = await asyncio.wait(
            {web, polling},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in done:
            await task

    finally:
        if server:
            server.should_exit = True

        if polling and not polling.done():
            try:
                await asyncio.wait_for(dp.stop_polling(), 15)
            except (RuntimeError, asyncio.TimeoutError):
                polling.cancel()

        if web and not web.done():
            try:
                await asyncio.wait_for(asyncio.shield(web), 15)
            except asyncio.TimeoutError:
                web.cancel()

        tasks = [task for task in (web, polling) if task]

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
