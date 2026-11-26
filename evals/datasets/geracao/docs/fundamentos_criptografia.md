# Fundamentos de Criptografia

## Criptografia simétrica

Na criptografia simétrica, a mesma chave secreta é usada para cifrar e para decifrar a mensagem. O AES é um exemplo de algoritmo simétrico. O principal desafio desse modelo é distribuir a chave com segurança entre as partes.

## Criptografia assimétrica

Na criptografia assimétrica, cada parte possui um par de chaves: uma chave pública, que pode ser divulgada, e uma chave privada, que deve ser mantida em sigilo. Uma mensagem cifrada com a chave pública de um destinatário só pode ser decifrada com a chave privada correspondente. O RSA é um exemplo de algoritmo assimétrico.

## Funções de hash

Uma função de hash criptográfica transforma uma entrada de qualquer tamanho em uma saída de tamanho fixo. Pequenas alterações na entrada produzem saídas muito diferentes. Funções de hash não são cifras: não existe chave para recuperar a entrada a partir da saída.
