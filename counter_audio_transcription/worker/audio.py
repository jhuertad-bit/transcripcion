import subprocess


def normalize(source, target, timeout=600, max_seconds=7200):
    """No elimina pausas. El downmix es señal ambiental, no separación de voces."""
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), "-map", "0:a:0", "-t", str(max_seconds+1), "-vn", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(target)], check=True, timeout=timeout,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    import soundfile as sf
    info = sf.info(str(target))
    if info.frames == 0 or info.samplerate != 16000 or info.channels != 1:
        raise ValueError("Audio vacío o normalización inválida")
    if info.duration > max_seconds:
        raise ValueError("Audio excede MAX_AUDIO_SECONDS; no se publica una transcripción truncada")
    return info.duration
