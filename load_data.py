import os
import torch
from scipy.sparse import csr_matrix
import numpy as np
from collections import defaultdict
import random
class DataLoader_DisGeNet:
    def __init__(self, args):
        self.args = args
        self.task_dir = task_dir = args.data_path

        with open(os.path.join(task_dir, 'relations.txt')) as f:
            self.relation2id = dict()
            n_rel = 0
            for line in f:
                relation, id = line.strip().split('\t')
                self.relation2id[relation] =n_rel #0-6
                n_rel += 1  #7

        self.n_rel = n_rel

        self.n_ent = 0
        self.entity2id = dict()

        # prepare triples
        self.filters = defaultdict(lambda:set())

        self.BKG_list=BKG_list= args.BKG_list
        self.fact_triple = self.read_BKG_triples(BKG_list,max_triples=args.max_BKG_triples)
        self.train_triple = self.read_triples(f'train.txt')
        self.valid_triple = self.read_triples(f'valid.txt')
        self.test_triple  = self.read_triples(f'test.txt')

        self.id2entity = {v: k for k, v in self.entity2id.items()}
        self.id2relation = {v: k for k, v in self.relation2id.items()}

        self.all_triple = np.concatenate([np.array(self.fact_triple), np.array(self.train_triple)], axis=0)
        self.tmp_all_triple = np.concatenate([np.array(self.fact_triple), np.array(self.train_triple), np.array(self.valid_triple), np.array(self.test_triple)], axis=0)
        
        # add inverse
        self.fact_data  = self.double_triple(self.fact_triple)
        self.train_data = np.array(self.double_triple(self.train_triple))
        self.valid_data = self.double_triple(self.valid_triple)
        self.test_data  = self.double_triple(self.test_triple)

        self.shuffle_train()
        self.load_graph(self.fact_data)
        self.load_test_graph(self.double_triple(self.fact_triple)+self.double_triple(self.train_triple))
        self.valid_q, self.valid_a = self.load_query(self.valid_data)
        self.test_q,  self.test_a  = self.load_query(self.test_data)

        self.n_train = len(self.train_data)
        self.n_valid = len(self.valid_q)
        self.n_test  = len(self.test_q)

        for filt in self.filters:
            self.filters[filt] = list(self.filters[filt])

        print('n_train:', self.n_train, 'n_valid:', self.n_valid, 'n_test:', self.n_test, 'n_ent:', self.n_ent, 'n_rel:', self.n_rel)

    def read_triples(self, filename):
        triples = []
        with open(os.path.join(self.task_dir, filename)) as f:
            for line in f:
                h, r, t = line.strip().split()

                if h not in self.entity2id:
                    self.entity2id[h] = self.n_ent
                    self.n_ent += 1

                if t not in self.entity2id:
                    self.entity2id[t] = self.n_ent
                    self.n_ent += 1


                h_id = self.entity2id[h]
                r_id = self.relation2id[r]
                t_id = self.entity2id[t]

                triples.append([h_id, r_id, t_id])
                self.filters[(h_id, r_id)].add(t_id)
                self.filters[(t_id, r_id + self.n_rel)].add(h_id)

        return triples


    def read_BKG_triples(self, filenames,max_triples=10000):
        triples = []
        for filename in filenames:
            file_triples = []
            with open(os.path.join(self.task_dir, '../facts', filename)) as f:
                for line in f:
                    h, r, t = line.strip().split('\t')
                    if h not in self.entity2id:
                        self.entity2id[h] = self.n_ent
                        self.n_ent += 1

                    if t not in self.entity2id:
                        self.entity2id[t] = self.n_ent
                        self.n_ent += 1


                        # 获取对应的 ID
                        h_id = self.entity2id[h]
                        r_id = self.relation2id[r]
                        t_id = self.entity2id[t]

                        file_triples.append([h_id, r_id, t_id])
                        self.filters[(h_id, r_id)].add(t_id)
                        self.filters[(t_id, r_id + self.n_rel)].add(h_id)

            # If the number of triples exceeds the maximum limit, randomly sample the maximum number of triples
            if max_triples!=-1 and len(file_triples) > max_triples:
                file_triples = random.sample(file_triples, max_triples)

            triples.extend(file_triples)
        return triples

    def double_triple(self, triples):
        new_triples = []
        for triple in triples:
            h, r, t = triple
            new_triples.append([t, r+self.n_rel, h]) 
        return triples + new_triples

    def load_graph(self, triples):
        # (e, r', e)
        # r' = 2 * n_rel, r' is manual generated and not exist in the original KG
        # self.KG: shape=(self.n_fact, 3)
        # M_sub shape=(self.n_fact, self.n_ent), store projection from head entity to triples
        idd = np.concatenate([np.expand_dims(np.arange(self.n_ent),1), 2*self.n_rel*np.ones((self.n_ent, 1)), np.expand_dims(np.arange(self.n_ent),1)], 1)

        self.KG = np.concatenate([np.array(triples), idd], 0)
        self.n_fact = len(self.KG)
        self.M_sub = csr_matrix((np.ones((self.n_fact,)), (np.arange(self.n_fact), self.KG[:,0])), shape=(self.n_fact, self.n_ent))

    def load_test_graph(self, triples):
        idd = np.concatenate([np.expand_dims(np.arange(self.n_ent),1), 2*self.n_rel*np.ones((self.n_ent, 1)), np.expand_dims(np.arange(self.n_ent),1)], 1)

        self.tKG = np.concatenate([np.array(triples), idd], 0)
        self.tn_fact = len(self.tKG)
        self.tM_sub = csr_matrix((np.ones((self.tn_fact,)), (np.arange(self.tn_fact), self.tKG[:,0])), shape=(self.tn_fact, self.n_ent))

    def load_query(self, triples):
        trip_hr = defaultdict(lambda:list())

        for trip in triples:
            h, r, t = trip
            trip_hr[(h,r)].append(t)
        
        queries = []
        answers = []
        for key in trip_hr:
            queries.append(key)
            answers.append(np.array(trip_hr[key]))
        return queries, answers

    def get_neighbors(self, nodes, batchsize, mode='train'):
        if mode == 'train':
            KG    = self.KG #  [N_fact, 3] with (head, rela, tail)
            M_sub = self.M_sub # [N_fact, N_ent]
        else:
            KG    = self.tKG
            M_sub = self.tM_sub

        # nodes: [N_ent_of_all_batch_last, 2] with (batch_idx, node_idx)
        # [N_ent, N_ent_of_all_batch_last]
        node_1hot     = csr_matrix((np.ones(len(nodes)), (nodes[:,1], nodes[:,0])), shape=(self.n_ent, nodes.shape[0]))
        # [N_fact, N_ent] * [N_ent, N_ent_of_all_batch_last] -> [N_fact, N_ent_of_all_batch_last]
        edge_1hot     = M_sub.dot(node_1hot)
        # [2, N_edge_of_all_batch] with (fact_idx, batch_idx)
        edges         = np.nonzero(edge_1hot)
        # {batch_idx} + {head, rela, tail} -> concat -> [N_edge_of_all_batch, 4] with (batch_idx, head, rela, tail)
        sampled_edges = np.concatenate([np.expand_dims(edges[1],1), KG[edges[0]]], axis=1)
        sampled_edges = torch.LongTensor(sampled_edges).cuda()

        # indexing nodes | within/out of a batch | relative index
        # note that node_idx is the absolute nodes idx in original KG
        # head_nodes: [N_ent_of_all_batch_last, 2] with (batch_idx, node_idx)
        # tail_nodes: [N_ent_of_all_batch_this, 2] with (batch_idx, node_idx)
        # head_index: [N_edge_of_all_batch] with relative node idx
        # tail_index: [N_edge_of_all_batch] with relative node idx
        head_nodes, head_index = torch.unique(sampled_edges[:,[0,1]], dim=0, sorted=True, return_inverse=True) # head_nodes.shape: torch.Size([20, 2]) with (batch_idx, node_idx)
        tail_nodes, tail_index = torch.unique(sampled_edges[:,[0,3]], dim=0, sorted=True, return_inverse=True) # tail_nodes.shape: torch.Size([N_ent_of_all_batch_this, 2]) with (batch_idx, node_idx)

        # [N_edge_of_all_batch, 4] -> [N_edge_of_all_batch, 6] with (batch_idx, head, rela, tail, head_index, tail_index)
        # node that the head_index and tail_index are of this layer
        sampled_edges = torch.cat([sampled_edges, head_index.unsqueeze(1), tail_index.unsqueeze(1)], 1)

        # get new index for nodes in last layer
        mask = sampled_edges[:,2] == (self.n_rel*2)
        # old_nodes_new_idx: [N_ent_of_all_batch_last]
        old_nodes_new_idx = tail_index[mask].sort()[0]

        return tail_nodes, sampled_edges, old_nodes_new_idx

    def get_batch(self, batch_idx, steps=2, data='train'):
        if data=='train':
            return np.array(self.train_data)[batch_idx]
        if data=='valid':
            query, answer = np.array(self.valid_q), np.array(self.valid_a, dtype=object)
        if data=='test':
            query, answer = np.array(self.test_q), np.array(self.test_a, dtype=object)
        subs = query[batch_idx, 0]
        rels = query[batch_idx, 1]
        objs = np.zeros((len(batch_idx), self.n_ent))
        for i in range(len(batch_idx)):
            objs[i][answer[batch_idx[i]]] = 1

        if data!='train':
            nonzero_indices=np.nonzero(objs)
            rows = nonzero_indices[0]
            num = []
            unique_rows, counts = np.unique(rows, return_counts=True)
            num = list(counts)
            return subs, rels, objs, num

        return subs, rels, objs

    def shuffle_train(self):
        all_triple = self.all_triple
        n_all = len(all_triple)
        rand_idx = np.random.permutation(n_all)
        all_triple = all_triple[rand_idx]

        bar = int(n_all * self.args.fact_ratio)
        self.fact_data = np.array(self.double_triple(all_triple[:bar].tolist()))
        self.train_data = np.array(self.double_triple(all_triple[bar:].tolist()))

        if hasattr(self.args, 'remove_1hop_edges') and self.args.remove_1hop_edges:
            print('==> removing 1-hop links...')
            tmp_index = np.ones((self.n_ent, self.n_ent))
            tmp_index[self.train_data[:, 0], self.train_data[:, 2]] = 0
            save_facts = tmp_index[self.fact_data[:, 0], self.fact_data[:, 2]].astype(bool)
            self.fact_data = self.fact_data[save_facts]
            print('==> done')

        self.n_train = len(self.train_data)
        self.load_graph(self.fact_data)


class DataLoader_STITCH:
    def __init__(self, args):
        self.args = args
        self.task_dir = args.data_path

        self.n_ent = 0
        self.n_rel = 0
        self.entity2id = dict()
        self.relation2id = dict()

        # prepare triples
        self.filters = defaultdict(lambda: set())

        self.BKG_list = BKG_list = args.BKG_list
        self.fact_triple = self.read_BKG_triples(
            BKG_list, max_triples=args.max_BKG_triples)
        self.train_triple = self.read_triples(f'train.txt')
        self.valid_triple = self.read_triples(f'valid.txt')
        self.test_triple = self.read_triples(f'test.txt')

        self.id2entity = {v: k for k, v in self.entity2id.items()}
        self.id2relation = {v: k for k, v in self.relation2id.items()}

        self.all_triple = np.concatenate(
            [np.array(self.fact_triple), np.array(self.train_triple)], axis=0)
        self.tmp_all_triple = np.concatenate([np.array(self.fact_triple), np.array(
            self.train_triple), np.array(self.valid_triple), np.array(self.test_triple)], axis=0)

        self.add_reverse_relations(self.train_triple)
        self.add_reverse_relations(self.valid_triple)
        self.add_reverse_relations(self.test_triple)

        self.fact_data = self.double_triple(self.fact_triple)
        self.train_data = np.array(self.double_triple(self.train_triple))
        self.valid_data = self.double_triple(self.valid_triple)
        self.test_data = self.double_triple(self.test_triple)

        self.shuffle_train()
        self.load_graph(self.fact_data)
        self.load_test_graph(self.double_triple(
            self.fact_triple)+self.double_triple(self.train_triple))
        # self.load_test_graph(self.double_triple(self.train_triple))
        self.valid_q, self.valid_a = self.load_query(self.valid_data)
        self.test_q,  self.test_a = self.load_query(self.test_data)

        self.n_train = len(self.train_data)
        self.n_valid = len(self.valid_q)
        self.n_test = len(self.test_q)

        for filt in self.filters:
            self.filters[filt] = list(self.filters[filt])

        print('n_train:', self.n_train, 'n_valid:', self.n_valid, 'n_test:',
              self.n_test, 'n_ent:', self.n_ent, 'n_rel:', self.n_rel)

    def add_reverse_relations(self, triple_list):
        for h_id, r_id, t_id in triple_list:
            self.filters[(t_id, r_id + self.n_rel)].add(h_id)

    def read_triples(self, filename):
        triples = []
        with open(os.path.join(self.task_dir, filename)) as f:
            for line in f:
                h, r, t = line.strip().split()

                if h not in self.entity2id:
                    self.entity2id[h] = self.n_ent
                    self.n_ent += 1

                if t not in self.entity2id:
                    self.entity2id[t] = self.n_ent
                    self.n_ent += 1

                if r not in self.relation2id:
                    self.relation2id[r] = self.n_rel
                    self.n_rel += 1

                h_id = self.entity2id[h]
                r_id = self.relation2id[r]
                t_id = self.entity2id[t]

                triples.append([h_id, r_id, t_id])
                self.filters[(h_id, r_id)].add(t_id)
                # self.filters[(t_id, r_id + self.n_rel)].add(h_id)

        return triples

    def read_BKG_triples(self, filenames, max_triples=10000):
        triples = []
        for filename in filenames:
            file_triples = []
            with open(os.path.join(self.task_dir, '../facts', filename)) as f:
                for line in f:
                    h, r, t = line.strip().split('\t')
                    if h not in self.entity2id:
                        self.entity2id[h] = self.n_ent
                        self.n_ent += 1

                    if t not in self.entity2id:
                        self.entity2id[t] = self.n_ent
                        self.n_ent += 1

                    if r not in self.relation2id:
                        self.relation2id[r] = self.n_rel
                        self.n_rel += 1

                        h_id = self.entity2id[h]
                        r_id = self.relation2id[r]
                        t_id = self.entity2id[t]

                        file_triples.append([h_id, r_id, t_id])
                        self.filters[(h_id, r_id)].add(t_id)
                        # self.filters[(t_id, r_id + self.n_rel)].add(h_id)

            # If the number of triples exceeds the maximum limit, randomly sample the maximum number of triples
            if max_triples != -1 and len(file_triples) > max_triples:
                file_triples = random.sample(file_triples, max_triples)

            triples.extend(file_triples)
        return triples

    def double_triple(self, triples):
        new_triples = []
        for triple in triples:
            h, r, t = triple
            new_triples.append([t, r+self.n_rel, h])
        return triples + new_triples

    def load_graph(self, triples):
        # (e, r', e)
        # r' = 2 * n_rel, r' is manual generated and not exist in the original KG
        # self.KG: shape=(self.n_fact, 3)
        # M_sub shape=(self.n_fact, self.n_ent), store projection from head entity to triples
        idd = np.concatenate([np.expand_dims(np.arange(self.n_ent), 1), 2*self.n_rel *
                             np.ones((self.n_ent, 1)), np.expand_dims(np.arange(self.n_ent), 1)], 1)

        self.KG = np.concatenate([np.array(triples), idd], 0)
        self.n_fact = len(self.KG)
        self.M_sub = csr_matrix((np.ones((self.n_fact,)), (np.arange(
            self.n_fact), self.KG[:, 0])), shape=(self.n_fact, self.n_ent))

    def load_test_graph(self, triples):
        idd = np.concatenate([np.expand_dims(np.arange(self.n_ent), 1), 2*self.n_rel *
                             np.ones((self.n_ent, 1)), np.expand_dims(np.arange(self.n_ent), 1)], 1)

        self.tKG = np.concatenate([np.array(triples), idd], 0)
        self.tn_fact = len(self.tKG)
        self.tM_sub = csr_matrix((np.ones((self.tn_fact,)), (np.arange(
            self.tn_fact), self.tKG[:, 0])), shape=(self.tn_fact, self.n_ent))

    def load_query(self, triples):
        trip_hr = defaultdict(lambda: list())

        for trip in triples:
            h, r, t = trip
            trip_hr[(h, r)].append(t)

        queries = []
        answers = []
        for key in trip_hr:
            queries.append(key)
            answers.append(np.array(trip_hr[key]))
        return queries, answers

    def get_neighbors(self, nodes, batchsize, mode='train'):
        if mode == 'train':
            KG = self.KG  # [N_fact, 3] with (head, rela, tail)
            M_sub = self.M_sub  # [N_fact, N_ent]
        else:
            KG = self.tKG
            M_sub = self.tM_sub

        # nodes: [N_ent_of_all_batch_last, 2] with (batch_idx, node_idx)
        # [N_ent, N_ent_of_all_batch_last]
        node_1hot = csr_matrix((np.ones(len(nodes)), (nodes[:, 1], nodes[:, 0])), shape=(
            self.n_ent, nodes.shape[0]))
        # [N_fact, N_ent] * [N_ent, N_ent_of_all_batch_last] -> [N_fact, N_ent_of_all_batch_last]
        edge_1hot = M_sub.dot(node_1hot)
        # [2, N_edge_of_all_batch] with (fact_idx, batch_idx)
        edges = np.nonzero(edge_1hot)
        # {batch_idx} + {head, rela, tail} -> concat -> [N_edge_of_all_batch, 4] with (batch_idx, head, rela, tail)
        sampled_edges = np.concatenate(
            [np.expand_dims(edges[1], 1), KG[edges[0]]], axis=1)
        sampled_edges = torch.LongTensor(sampled_edges).cuda()

        # indexing nodes | within/out of a batch | relative index
        # note that node_idx is the absolute nodes idx in original KG
        # head_nodes: [N_ent_of_all_batch_last, 2] with (batch_idx, node_idx)
        # tail_nodes: [N_ent_of_all_batch_this, 2] with (batch_idx, node_idx)
        # head_index: [N_edge_of_all_batch] with relative node idx
        # tail_index: [N_edge_of_all_batch] with relative node idx
        # head_nodes.shape: torch.Size([20, 2]) with (batch_idx, node_idx)
        head_nodes, head_index = torch.unique(
            sampled_edges[:, [0, 1]], dim=0, sorted=True, return_inverse=True)
        # tail_nodes.shape: torch.Size([N_ent_of_all_batch_this, 2]) with (batch_idx, node_idx) 
        tail_nodes, tail_index = torch.unique(
            sampled_edges[:, [0, 3]], dim=0, sorted=True, return_inverse=True)

        # [N_edge_of_all_batch, 4] -> [N_edge_of_all_batch, 6] with (batch_idx, head, rela, tail, head_index, tail_index)
        # node that the head_index and tail_index are of this layer
        sampled_edges = torch.cat(
            [sampled_edges, head_index.unsqueeze(1), tail_index.unsqueeze(1)], 1)

        # get new index for nodes in last layer
        mask = sampled_edges[:, 2] == (self.n_rel*2)
        old_nodes_new_idx = tail_index[mask].sort()[0]

        return tail_nodes, sampled_edges, old_nodes_new_idx

    def get_batch(self, batch_idx, steps=2, data='train'):
        if data == 'train':
            return np.array(self.train_data)[batch_idx]
        if data == 'valid':
            query, answer = np.array(self.valid_q), np.array(
                self.valid_a, dtype=object)
        if data == 'test':
            query, answer = np.array(self.test_q), np.array(
                self.test_a, dtype=object)

        subs = query[batch_idx, 0]
        rels = query[batch_idx, 1]
        objs = np.zeros((len(batch_idx), self.n_ent))
        for i in range(len(batch_idx)):
            objs[i][answer[batch_idx[i]]] = 1

        if data != 'train':
            nonzero_indices = np.nonzero(objs)
            rows = nonzero_indices[0]
            num = []
            unique_rows, counts = np.unique(rows, return_counts=True)
            num = list(counts)
            return subs, rels, objs, num

        return subs, rels, objs

    def shuffle_train(self):
        all_triple = self.all_triple
        n_all = len(all_triple)
        rand_idx = np.random.permutation(n_all)
        all_triple = all_triple[rand_idx]

        bar = int(n_all * self.args.fact_ratio)
        self.fact_data = np.array(
            self.double_triple(all_triple[:bar].tolist()))
        self.train_data = np.array(
            self.double_triple(all_triple[bar:].tolist()))

        if hasattr(self.args, 'remove_1hop_edges') and self.args.remove_1hop_edges:
            print('==> removing 1-hop links...')
            tmp_index = np.ones((self.n_ent, self.n_ent))
            tmp_index[self.train_data[:, 0], self.train_data[:, 2]] = 0
            save_facts = tmp_index[self.fact_data[:, 0],
                                   self.fact_data[:, 2]].astype(bool)
            self.fact_data = self.fact_data[save_facts]
            print('==> done')

        self.n_train = len(self.train_data)
        self.load_graph(self.fact_data)


class DataLoader_UMLS:
    def __init__(self, args):
        self.args = args
        self.task_dir = task_dir = args.data_path

        with open(os.path.join(task_dir, 'entities.txt')) as f:
            self.entity2id = dict()
            n_ent = 0
            for line in f:
                entity = line.strip()
                self.entity2id[entity] = n_ent
                n_ent += 1

        with open(os.path.join(task_dir, 'relations.txt')) as f:
            self.relation2id = dict()
            n_rel = 0
            for line in f:
                relation = line.strip()
                self.relation2id[relation] = n_rel
                n_rel += 1

        self.n_ent = n_ent
        self.n_rel = n_rel

        # prepare triples
        self.filters = defaultdict(lambda: set())
        self.fact_triple = self.read_triples('facts.txt')
        self.train_triple = self.read_triples('train.txt')
        self.valid_triple = self.read_triples('valid.txt')
        self.test_triple = self.read_triples('test.txt')

        self.id2entity = {v: k for k, v in self.entity2id.items()}
        self.id2relation = {v: k for k, v in self.relation2id.items()}

        self.all_triple = np.concatenate(
            [np.array(self.fact_triple), np.array(self.train_triple)], axis=0)
        self.tmp_all_triple = np.concatenate([np.array(self.fact_triple), np.array(
            self.train_triple), np.array(self.valid_triple), np.array(self.test_triple)], axis=0)

        # add inverse
        self.fact_data = self.double_triple(self.fact_triple)
        self.train_data = np.array(self.double_triple(self.train_triple))
        self.valid_data = self.double_triple(self.valid_triple)
        self.test_data = self.double_triple(self.test_triple)

        self.shuffle_train()
        self.load_graph(self.fact_data)
        self.load_test_graph(self.double_triple(
            self.fact_triple)+self.double_triple(self.train_triple))
        self.valid_q, self.valid_a = self.load_query(self.valid_data)
        self.test_q,  self.test_a = self.load_query(self.test_data)

        self.n_train = len(self.train_data)
        self.n_valid = len(self.valid_q)
        self.n_test = len(self.test_q)

        for filt in self.filters:
            self.filters[filt] = list(self.filters[filt])

        print('n_train:', self.n_train, 'n_valid:',
              self.n_valid, 'n_test:', self.n_test)

    def read_triples(self, filename):
        triples = []
        with open(os.path.join(self.task_dir, filename)) as f:
            for line in f:
                h, r, t = line.strip().split()
                h, r, t = self.entity2id[h], self.relation2id[r], self.entity2id[t]
                triples.append([h, r, t])
                self.filters[(h, r)].add(t)
                self.filters[(t, r+self.n_rel)].add(h)
        return triples

    def double_triple(self, triples):
        new_triples = []
        for triple in triples:
            h, r, t = triple
            new_triples.append([t, r+self.n_rel, h])
        return triples + new_triples

    def load_graph(self, triples):
        # (e, r', e)
        # r' = 2 * n_rel, r' is manual generated and not exist in the original KG
        # self.KG: shape=(self.n_fact, 3)
        # M_sub shape=(self.n_fact, self.n_ent), store projection from head entity to triples
        idd = np.concatenate([np.expand_dims(np.arange(self.n_ent), 1), 2*self.n_rel*np.ones(
            (self.n_ent, 1)), np.expand_dims(np.arange(self.n_ent), 1)], 1)  # identity triples

        self.KG = np.concatenate([np.array(triples), idd], 0)
        self.n_fact = len(self.KG)
        self.M_sub = csr_matrix((np.ones((self.n_fact,)), (np.arange(self.n_fact), self.KG[:, 0])), shape=(
            self.n_fact, self.n_ent))  

    def load_test_graph(self, triples):
        idd = np.concatenate([np.expand_dims(np.arange(self.n_ent), 1), 2*self.n_rel *
                             np.ones((self.n_ent, 1)), np.expand_dims(np.arange(self.n_ent), 1)], 1)

        self.tKG = np.concatenate([np.array(triples), idd], 0)
        self.tn_fact = len(self.tKG)
        self.tM_sub = csr_matrix((np.ones((self.tn_fact,)), (np.arange(
            self.tn_fact), self.tKG[:, 0])), shape=(self.tn_fact, self.n_ent))

    def load_query(self, triples):
        trip_hr = defaultdict(lambda: list())

        for trip in triples:
            h, r, t = trip
            trip_hr[(h, r)].append(t)

        queries = []
        answers = []
        for key in trip_hr:
            queries.append(key)
            answers.append(np.array(trip_hr[key]))
        return queries, answers

    def get_neighbors(self, nodes, batchsize, mode='train'):
        if mode == 'train':
            KG = self.KG
            M_sub = self.M_sub
        else:
            KG = self.tKG
            M_sub = self.tM_sub

        # nodes: [N_ent_of_all_batch_last, 2] with (batch_idx, node_idx)
        # [N_ent, N_ent_of_all_batch_last]
        node_1hot = csr_matrix((np.ones(len(nodes)), (nodes[:, 1], nodes[:, 0])), shape=(
            self.n_ent, nodes.shape[0]))
        # [N_fact, N_ent] * [N_ent, N_ent_of_all_batch_last] -> [N_fact, N_ent_of_all_batch_last]
        edge_1hot = M_sub.dot(node_1hot)
        # [2, N_edge_of_all_batch] with (fact_idx, batch_idx)
        edges = np.nonzero(edge_1hot)
        # {batch_idx} + {head, rela, tail} -> concat -> [N_edge_of_all_batch, 4] with (batch_idx, head, rela, tail)
        sampled_edges = np.concatenate(
            [np.expand_dims(edges[1], 1), KG[edges[0]]], axis=1)
        sampled_edges = torch.LongTensor(sampled_edges).cuda()

        # indexing nodes | within/out of a batch | relative index
        # note that node_idx is the absolute nodes idx in original KG
        # head_nodes: [N_ent_of_all_batch_last, 2] with (batch_idx, node_idx)
        # tail_nodes: [N_ent_of_all_batch_this, 2] with (batch_idx, node_idx)
        # head_index: [N_edge_of_all_batch] with relative node idx
        # tail_index: [N_edge_of_all_batch] with relative node idx
        head_nodes, head_index = torch.unique(
            sampled_edges[:, [0, 1]], dim=0, sorted=True, return_inverse=True)
        tail_nodes, tail_index = torch.unique(
            sampled_edges[:, [0, 3]], dim=0, sorted=True, return_inverse=True)

        # [N_edge_of_all_batch, 4] -> [N_edge_of_all_batch, 6] with (batch_idx, head, rela, tail, head_index, tail_index)
        # node that the head_index and tail_index are of this layer
        sampled_edges = torch.cat(
            [sampled_edges, head_index.unsqueeze(1), tail_index.unsqueeze(1)], 1)

        # get new index for nodes in last layer
        mask = sampled_edges[:, 2] == (self.n_rel*2)
        # old_nodes_new_idx: [N_ent_of_all_batch_last]
        old_nodes_new_idx = tail_index[mask].sort()[0]

        return tail_nodes, sampled_edges, old_nodes_new_idx

    def get_batch(self, batch_idx, steps=2, data='train'):
        if data == 'train':
            return np.array(self.train_data)[batch_idx]
        if data == 'valid':
            query, answer = np.array(self.valid_q), np.array(
                self.valid_a, dtype=object)
        if data == 'test':
            query, answer = np.array(self.test_q), np.array(
                self.test_a, dtype=object)
        subs = []
        rels = []
        objs = []
        subs = query[batch_idx, 0]
        rels = query[batch_idx, 1]
        objs = np.zeros((len(batch_idx), self.n_ent))
        for i in range(len(batch_idx)):
            objs[i][answer[batch_idx[i]]] = 1
        if data != 'train':
            nonzero_indices = np.nonzero(objs)
            rows = nonzero_indices[0]
            num = []
            unique_rows, counts = np.unique(rows, return_counts=True)
            num = list(counts)
            return subs, rels, objs, num
        return subs, rels, objs

    def shuffle_train(self):
        all_triple = self.all_triple
        n_all = len(all_triple)
        rand_idx = np.random.permutation(n_all)
        all_triple = all_triple[rand_idx]

        bar = int(n_all * self.args.fact_ratio)
        self.fact_data = np.array(
            self.double_triple(all_triple[:bar].tolist()))
        self.train_data = np.array(
            self.double_triple(all_triple[bar:].tolist()))

        if hasattr(self.args, 'remove_1hop_edges') and self.args.remove_1hop_edges:
            # if self.args.remove_1hop_edges:
            print('==> removing 1-hop links...')
            tmp_index = np.ones((self.n_ent, self.n_ent))
            tmp_index[self.train_data[:, 0], self.train_data[:, 2]] = 0
            save_facts = tmp_index[self.fact_data[:, 0],
                                   self.fact_data[:, 2]].astype(bool)
            self.fact_data = self.fact_data[save_facts]
            print('==> done')

        self.n_train = len(self.train_data)
        self.load_graph(self.fact_data)
