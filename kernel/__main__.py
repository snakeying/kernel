import asyncio
import logging
import sys
log = logging.getLogger('kernel')

def main() -> None:
    from kernel.tg_common import MaskingFormatter

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        MaskingFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    from kernel.bot import run_bot
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        log.info('Shutting down …')
if __name__ == '__main__':
    main()
