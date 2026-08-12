with ChromaTestContext("debug") as client:

    ok = client.agregar(
        documentos=["a", "b", "c"],
        metadatos=[
            {"idx": 0},
            {"idx": 1},
            {"idx": 2}
        ],
        ids=["x", "y", "z"],
    )

    print("agregar() =", ok)
    print("conteo =", client.contar())