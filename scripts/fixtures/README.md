# Fixture do instalador

`installer-video.mp4`: vídeo sintético preto, MPEG-4, 16×16, 0,2 segundo,
sem áudio ou conteúdo do operador. Gerado localmente por:

```powershell
ffmpeg -f lavfi -i color=c=black:s=16x16:d=0.2 -an -c:v mpeg4 installer-video.mp4
```

O ensaio apenas copia e confere o hash; não precisa de FFmpeg para executá-lo.
