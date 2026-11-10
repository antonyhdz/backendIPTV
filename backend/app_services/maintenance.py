import asyncio


# Serialize database maintenance (restore/backup) with background jobs.
maintenance_lock = asyncio.Lock()
