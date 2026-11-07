import zipfile

import pytest

from atena import config
from atena.cofre import cofres
from atena import cofre as cofre_mod
from atena.servicos import backup, codificacao as cod, documentos, projetos, protegido
from atena.servicos.projetos import ErroValidacao
from conftest import SENHA


def _popular(p):
    documentos.criar_manual(p, "Texto com Fulano Ficticio.", titulo="E1")
    cod.criar_codigo(p, "Código persistente")


def test_persistencia_apos_fechar_e_reabrir(projeto):
    _popular(projeto)
    uid = projeto.uid
    projeto.fechar()
    cofres.bloquear()
    p2 = projetos.Projeto(uid)
    assert [c["nome"] for c in cod.listar_codigos(p2)] == ["Código persistente"]
    cofres.guardar(uid, cofre_mod.abrir(p2.con_cofre, SENHA))
    assert "Fulano" in protegido.ler_texto(p2, 1)["texto"]
    p2.fechar()


def test_backup_e_restauracao_como_copia(projeto):
    _popular(projeto)
    arq = backup.criar_backup(projeto.uid, "manual", "teste")
    m = backup.validar(arq)
    assert m["uid"] == projeto.uid and "cofre.sqlite" in m["arquivos"]
    with zipfile.ZipFile(arq) as z:
        assert b"Fulano" not in z.read("cofre.sqlite")  # cofre continua cifrado no backup
    uid2 = backup.restaurar(arq, "copia")
    p2 = projetos.Projeto(uid2)
    assert "cópia restaurada" in p2.dados()["nome"]
    assert [c["nome"] for c in cod.listar_codigos(p2)] == ["Código persistente"]
    assert p2.con.execute("SELECT COUNT(*) FROM historico WHERE operacao='restaurar_backup'").fetchone()[0] == 1
    cofres.guardar(uid2, cofre_mod.abrir(p2.con_cofre, SENHA))
    assert "Fulano" in protegido.ler_texto(p2, 1)["texto"]
    p2.fechar()


def test_restauracao_substituindo_preserva_estado_anterior(projeto):
    _popular(projeto)
    uid = projeto.uid
    arq = backup.criar_backup(uid, "manual")
    cod.criar_codigo(projeto, "Criado depois do backup")
    projeto.fechar()
    backup.restaurar(arq, "substituir", "voltar ao backup")
    p2 = projetos.Projeto(uid)
    assert [c["nome"] for c in cod.listar_codigos(p2)] == ["Código persistente"]
    p2.fechar()
    guardados = list((config.pasta_dados() / "substituidos").iterdir())
    assert guardados and (guardados[0] / "projeto.sqlite").is_file()   # nada foi apagado
    assert any(b["tipo"] == "pre_restauracao" for b in backup.listar(uid))


def test_backup_corrompido_e_rejeitado(projeto, tmp_path):
    arq = backup.criar_backup(projeto.uid)
    ruim = tmp_path / "ruim.atenabak"
    with zipfile.ZipFile(arq) as zi, zipfile.ZipFile(ruim, "w") as zo:
        for n in zi.namelist():
            dados = zi.read(n)
            if n == "projeto.sqlite":
                dados = dados[:-100] + b"x" * 100
            zo.writestr(n, dados)
    with pytest.raises(ErroValidacao):
        backup.restaurar(ruim, "copia")
    (tmp_path / "falso.atenabak").write_bytes(b"nao e zip")
    with pytest.raises(ErroValidacao):
        backup.restaurar(tmp_path / "falso.atenabak")


def test_backup_automatico_somente_com_alteracoes(projeto):
    config.salvar({"backup_automatico": True, "backup_intervalo_horas": 0.0001})
    assert backup.executar_automaticos() == [projeto.uid]
    assert backup.executar_automaticos() == []            # nada mudou desde o último
    cod.criar_codigo(projeto, "novo")
    import time; time.sleep(0.5)
    assert backup.executar_automaticos() == [projeto.uid]


def test_backup_completo(projeto):
    arq = backup.backup_completo()
    with zipfile.ZipFile(arq) as z:
        assert any(n.endswith(".atenabak") for n in z.namelist())
