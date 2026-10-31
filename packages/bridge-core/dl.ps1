$ErrorActionPreference = "Continue"
& curl.exe -L -s -S -C - -o "E:\useer\deepseek-harness\default-workspace\packages\bridge-core\models\model.int8.onnx" "https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/model.int8.onnx"
Write-Output ("MODEL " + (Get-Item "E:\useer\deepseek-harness\default-workspace\packages\bridge-core\models\model.int8.onnx").Length)
& curl.exe -L -s -S -o "E:\useer\deepseek-harness\default-workspace\packages\bridge-core\models\tokens.txt" "https://hf-mirror.com/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/tokens.txt"
Write-Output ("TOKENS " + (Get-Item "E:\useer\deepseek-harness\default-workspace\packages\bridge-core\models\tokens.txt").Length)
Write-Output "DL-DONE"