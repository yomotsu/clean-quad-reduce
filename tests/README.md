# テスト

リポジトリのルートで実行します。Blender 5.1以降が必要です。`blender`は環境に応じて実行ファイルのパスに置き換えてください。

```sh
python3 build.py
blender --background --factory-startup --python-exit-code 1 --python tests/blender_checks.py
blender --command extension validate dist/clean_quad_reduce-1.0.0.zip
```

`blender_checks.py`はモデルを生成して、列の削減、斜め格子の拒否、UV・ウェイト・表情キーの保護、プレビュー、単独スキル実行、アドオン操作を43項目で検証します。外部モデルやVRMエクスポーターは不要です。出力はGit管理対象外の`test-output/`です。

## 外部の基準モデルによる任意の回帰検証

基準モデルはリポジトリに含めません。以下の検証にはそのモデルとVRM Add-on for Blenderが必要です。エクスポーターのモジュール名は`bl_ext.user_default.vrm`を想定しています。これらのスクリプトのモデル固有名は検証用であり、配布エンジンの条件分岐には使いません。

```sh
blender --background --factory-startup /path/to/Defeated_cleanquad_aligned.blend --python-exit-code 1 --python tests/aligned_reference_check.py
```

この完成モデルには、結果オブジェクト`Beta_Surface`と`Beta_Joints`、削減前のメッシュデータ`Beta_Surface_ORIGINAL`と`Beta_Joints_ORIGINAL`が必要です。削減をやり直し、頂点位置・面接続・向き、およびVRMの11,647頂点・19,044三角面を照合します。入力ファイルは上書きしません。

`real_model_check.py`はStrict方式の補助ベンチマークです。同名の2オブジェクトを持つ削減前モデル、または同スクリプトが保存したバックアップ付きモデルを開いて実行してください。Aligned方式との一致を確認する用途には`aligned_reference_check.py`を使います。
