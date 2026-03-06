from Bio import SeqIO
import sys


def split_single_genome(input_file, n_shards):
    record = next(SeqIO.parse(input_file, "fasta"))
    seq = str(record.seq)
    total = len(seq)
    shard_size = total // n_shards

    for i in range(n_shards):
        start = i * shard_size
        end = start + shard_size if i < n_shards - 1 else total
        chunk = seq[start:end]

        output_file = f"data/shard_{i + 1}.fasta"
        with open(output_file, "w") as f:
            f.write(f">{record.id}_shard{i + 1}\n{chunk}\n")
        print(f"Shard {i + 1}: {len(chunk)} bp → {output_file}")


split_single_genome(sys.argv[1], 4)
