Add-Type -AssemblyName System.Speech
foreach ($svAudioLanguage in @('en', 'cn')) {
    $svSynthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        if ($svAudioLanguage -eq 'cn') {
            $svSynthesizer.SelectVoice('Microsoft Huihui Desktop')
            $svAudioWords = [string]::Concat([char]0x4F60, [char]0x597D, [char]0xFF0C, [char]0x8FD9, [char]0x662F, [char]0x4E00, [char]0x4E2A, [char]0x79D8, [char]0x5BC6, [char]0x89C6, [char]0x9891, [char]0x901A, [char]0x4FE1, [char]0x6D4B, [char]0x8BD5, [char]0x3002)
        } else {
            $svSynthesizer.SelectVoice('Microsoft Zira Desktop')
            $svAudioWords = 'hello this is a secret video communication test'
        }
        $svSynthesizer.SetOutputToWaveFile((Join-Path $PSScriptRoot "secret-video-source-$svAudioLanguage.wav"))
        $svSynthesizer.Speak($svAudioWords)
    } finally {
        $svSynthesizer.SetOutputToNull()
        $svSynthesizer.Dispose()
    }
}
# Convert with the same Python environment as ISTG (set PATH/activate Conda first).
python -c "import imageio_ffmpeg,subprocess; [(subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-v','error','-i',f'.qa/secret-video-source-{lang}.wav','-af','apad=pad_dur=4','-ar','48000','-ac','1','-c:a','pcm_s16le',f'.qa/{out}'],check=True)) for lang,out in [('en','secret-video-mic.wav'),('cn','secret-video-zh-mic.wav')]]"
