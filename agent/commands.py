"""Bounded, non-interactive host commands. Host access remains opt-in."""
import asyncio
import os
import signal
import time

OUTPUT_LIMIT = 64 * 1024
HOST_SHELL = ('nsenter', '--target', '1', '--mount', '--uts', '--ipc', '--net', '--pid',
              '--root', '--wd=/proc/1/root', '--', '/bin/sh')
HOST_ENV = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            'HOME': '/root', 'LANG': 'C.UTF-8'}
_running = 0


async def run_command(command, timeout, *, host_access_enabled=False):
    global _running
    if not host_access_enabled:
        raise ValueError('Dostęp do hosta jest wyłączony (HOST_ACCESS_ENABLED).')
    if not isinstance(command, str) or not command.strip() or len(command) > 16000 or '\x00' in command:
        raise ValueError('Komenda musi zawierać od 1 do 16000 znaków, bez znaku NUL.')
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise ValueError('Limit czasu musi wynosić od 1 do 120 sekund.')
    if _running >= 4:
        raise ValueError('Agent wykonuje już 4 komendy. Poczekaj na zakończenie.')
    _running += 1
    started = time.monotonic()
    proc = None
    readers = []
    captured = {'stdout': bytearray(), 'stderr': bytearray()}
    truncated = False

    async def drain(stream, name):
        nonlocal truncated
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            remaining = OUTPUT_LIMIT - len(captured[name])
            captured[name].extend(chunk[:remaining])
            truncated = truncated or len(chunk) > remaining

    def kill():
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    try:
        proc = await asyncio.create_subprocess_exec(
            *HOST_SHELL, '-c', command,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, env=HOST_ENV,
        )
        readers = [asyncio.create_task(drain(proc.stdout, 'stdout')),
                   asyncio.create_task(drain(proc.stderr, 'stderr'))]
        timed_out = False
        try:
            await asyncio.wait_for(asyncio.gather(proc.wait(), *readers), timeout)
        except asyncio.TimeoutError:
            timed_out = True
            kill()
            # Detached descendants can retain stdout/stderr after their parent
            # dies. Closing the subprocess transport keeps timeout cleanup bounded.
            proc._transport.close()
            await proc.wait()
        return {
            'stdout': captured['stdout'].decode('utf-8', errors='replace'),
            'stderr': captured['stderr'].decode('utf-8', errors='replace'),
            'exit_code': proc.returncode, 'timed_out': timed_out,
            'truncated': truncated, 'duration_ms': round((time.monotonic() - started) * 1000),
        }
    finally:
        try:
            if proc is not None:
                kill()
                proc._transport.close()
                await proc.wait()
        finally:
            for task in readers:
                task.cancel()
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)
            _running -= 1
